/**
 * Builds the agent into `dist/`.
 *
 * The output is a folder that Chrome can load directly ("Load unpacked"), and
 * the packager turns it into a branded ZIP for a client.
 *
 * Three bundles are produced, because Chrome wants them in three different
 * shapes: the service worker is a module, the content script is a classic
 * script, and the side panel is a classic script with its own stylesheet.
 */

import { build } from "esbuild";
import {
  mkdir,
  copyFile,
  readFile,
  readdir,
  writeFile,
  rm,
} from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const dist = path.join(root, "dist");

/** The base manifest, with the client-agnostic defaults. */
const DEFAULT_TEMPLATE = {
  clientName: "WorkMeta AI",
  version: "0.1.0",
  description: "Guidance for the systems your organisation trained.",
  allowedDomains: [],
  apiOrigin: "http://127.0.0.1:8000",
  themeColor: "#0b1220",
};

async function bundle(entry, outfile, format) {
  await build({
    entryPoints: [path.join(root, entry)],
    outfile: path.join(dist, outfile),
    bundle: true,
    format,
    platform: "browser",
    target: ["chrome120"],
    sourcemap: false,
    minify: false,
    define: { "process.env.NODE_ENV": '"production"' },
    logLevel: "warning",
  });
}

/** Read the manifest template and write the manifest it produces. */
async function writeManifest() {
  const configUrl = new URL("../manifest.config.ts", import.meta.url);
  const source = await readFile(configUrl, "utf8");
  // The template is TypeScript, so it is compiled before it is used.
  const compiled = await build({
    stdin: {
      contents: source,
      resolveDir: root,
      loader: "ts",
      sourcefile: "manifest.config.ts",
    },
    bundle: true,
    write: false,
    format: "esm",
    platform: "neutral",
    target: ["es2022"],
    logLevel: "warning",
  });
  const encoded = Buffer.from(compiled.outputFiles[0].contents).toString(
    "base64",
  );
  const module = await import(`data:text/javascript;base64,${encoded}`);
  const manifest = module.buildManifest(DEFAULT_TEMPLATE);
  await writeFile(
    path.join(dist, "manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );
}

/** A development configuration, so the extension can be loaded unpacked. */
async function writeDevConfig() {
  const config = {
    apiBaseUrl: `${DEFAULT_TEMPLATE.apiOrigin}/api/v1`,
    orgId: "",
    allowedDomains: DEFAULT_TEMPLATE.allowedDomains,
    clientName: DEFAULT_TEMPLATE.clientName,
  };
  await writeFile(
    path.join(dist, "config.json"),
    `${JSON.stringify(config, null, 2)}\n`,
    "utf8",
  );
}

async function copyStatic() {
  const sidepanel = path.join(root, "src", "sidepanel");
  await copyFile(
    path.join(sidepanel, "index.html"),
    path.join(dist, "sidepanel.html"),
  );

  // The icons live in an `icons` folder, the way the manifest expects them.
  const icons = path.join(root, "public", "icons");
  if (existsSync(icons)) {
    const target = path.join(dist, "icons");
    await mkdir(target, { recursive: true });
    for (const name of await readdir(icons)) {
      await copyFile(path.join(icons, name), path.join(target, name));
    }
  }
}

async function main() {
  await rm(dist, { recursive: true, force: true });
  await mkdir(dist, { recursive: true });

  await bundle("src/background/index.ts", "background.js", "esm");
  await bundle("src/content/index.ts", "content.js", "iife");
  await bundle("src/sidepanel/app.ts", "sidepanel.js", "iife");
  await copyFile(
    path.join(root, "src", "sidepanel", "styles.css"),
    path.join(dist, "sidepanel.css"),
  );

  await writeManifest();
  await writeDevConfig();
  await copyStatic();

  process.stdout.write(`Built the agent into ${dist}\n`);
}

await main();
