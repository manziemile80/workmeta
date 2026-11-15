/**
 * A check that runs the real package through the browser's verification.
 *
 * The package is produced by `tools/scripts/pack_extension.py`; this test
 * reads it and checks the signature with the same code the agent uses, so the
 * two halves are proven to agree.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { verifySignedBytes } from "../src/lib/signature.ts";

Object.defineProperty(globalThis, "crypto", {
  value: webcrypto,
  configurable: true,
});

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");

/**
 * The Python that has the project's dependencies.
 *
 * The packager and the signing key live in the API's virtual environment, so
 * that is the interpreter the test uses.
 */
const python = (() => {
  const candidates = [
    path.join(repoRoot, "apps", "api", ".venv", "Scripts", "python.exe"),
    path.join(repoRoot, "apps", "api", ".venv", "bin", "python"),
    "python",
  ];
  for (const candidate of candidates) {
    try {
      execFileSync(candidate, ["-c", "import cryptography"], {
        stdio: "ignore",
      });
      return candidate;
    } catch {
      // Try the next one.
    }
  }
  return "python";
})();

/**
 * Build a signed package for a test client.
 *
 * The key is generated here, so the test does not depend on a deployment.
 */
function buildSignedPackage(): {
  config: Uint8Array;
  signature: string;
  publicKey: string;
} {
  const key = execFileSync(
    python,
    [
      "-c",
      [
        "from cryptography.hazmat.primitives.asymmetric.ed25519 import ",
        "Ed25519PrivateKey;",
        "from cryptography.hazmat.primitives import serialization;",
        "k = Ed25519PrivateKey.generate();",
        "print(k.private_bytes(",
        "encoding=serialization.Encoding.PEM,",
        "format=serialization.PrivateFormat.PKCS8,",
        "encryption_algorithm=serialization.NoEncryption()).decode())",
      ].join(" "),
    ],
    { encoding: "utf8" },
  ).trim();

  const workdir = mkdtempSync(path.join(tmpdir(), "workmeta-package-"));
  try {
    const zipPath = path.join(workdir, "package.zip");
    execFileSync(
      python,
      [
        path.join(repoRoot, "tools", "scripts", "pack_extension.py"),
        "--client-name",
        "Test Client",
        "--api-origin",
        "https://api.test.invalid",
        "--org-id",
        "org-1",
        "--domain",
        "ebm.test.invalid",
        "--out",
        zipPath,
      ],
      {
        cwd: repoRoot,
        encoding: "utf8",
        env: {
          ...process.env,
          EXTENSION_SIGNING_PRIVATE_KEY: key,
          EXTENSION_SIGNING_KEY_ID: "test-key-1",
        },
      },
    );

    // The ZIP is read with the same archive library the packager used.
    const entries = execFileSync(
      python,
      [
        "-c",
        [
          "import base64, sys, zipfile;",
          "z = zipfile.ZipFile(sys.argv[1]);",
          "print(base64.b64encode(z.read('config.json')).decode());",
          "print(z.read('config.sig').decode());",
          "print(z.read('config.pub').decode())",
        ].join(" "),
        zipPath,
      ],
      { encoding: "utf8" },
    )
      .trim()
      .split("\n");

    const config = Buffer.from(entries[0], "base64");
    return {
      config: Uint8Array.from(config),
      signature: entries[1],
      publicKey: entries[2],
    };
  } finally {
    rmSync(workdir, { recursive: true, force: true });
  }
}

test("the browser verifies the configuration a packaged agent carries", async () => {
  const packaged = buildSignedPackage();

  const check = await verifySignedBytes(
    packaged.config,
    packaged.signature,
    packaged.publicKey,
  );
  assert.equal(check.valid, true, check.reason);
  assert.ok(check.fingerprint.length > 0);

  // A changed configuration is refused.
  const changed = Uint8Array.from(packaged.config);
  changed[changed.length - 2] = changed[changed.length - 2] === 0x0a ? 0x20 : 0x0a;
  const refused = await verifySignedBytes(
    changed,
    packaged.signature,
    packaged.publicKey,
  );
  assert.equal(refused.valid, false);
});
