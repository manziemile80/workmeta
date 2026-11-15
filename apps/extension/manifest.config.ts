/**
 * The base manifest of the installed agent.
 *
 * The packager rewrites the parts that differ per client: the name, the
 * icons, the theme colour and the domains the agent may read. Everything
 * else is the same for every client, because there is one codebase.
 *
 * The permissions are deliberately small:
 *
 * - `storage`   keeps the session token in the browser profile.
 * - `sidePanel` shows the assistant.
 *
 * There is no `tabs` permission (which would reveal every address the user
 * visits) and no "all sites" host permission. The agent only ever sees the
 * addresses of the systems the client trained, because the packager lists
 * exactly those domains in `host_permissions`, and the agent checks them
 * again itself before it uses one.
 */

export interface ManifestTemplate {
  clientName: string;
  version: string;
  description: string;
  /** Domains the agent may read, one per trained system. */
  allowedDomains: string[];
  /** Where the agent sends its requests. */
  apiOrigin: string;
  themeColor: string;
}

export function buildManifest(
  template: ManifestTemplate,
): Record<string, unknown> {
  const hostPatterns = template.allowedDomains.map(
    (domain) => `https://${domain}/*`,
  );

  return {
    manifest_version: 3,
    name: `${template.clientName} Assistant`,
    short_name: template.clientName,
    version: template.version,
    description: template.description,
    // The agent is installed for one client, so it talks to one service.
    host_permissions: [`${new URL(template.apiOrigin).origin}/*`],
    permissions: ["storage", "sidePanel"],
    background: {
      service_worker: "background.js",
      type: "module",
    },
    action: {
      default_title: `Ask ${template.clientName}`,
      default_icon: {
        "16": "icons/icon-16.png",
        "32": "icons/icon-32.png",
        "48": "icons/icon-48.png",
        "128": "icons/icon-128.png",
      },
    },
    side_panel: {
      default_path: "sidepanel.html",
    },
    // The content script only runs on the client's own systems. It reports
    // which page is open and reads the visible text only when the user asks
    // a question about that page.
    content_scripts: [
      {
        matches: hostPatterns,
        js: ["content.js"],
        run_at: "document_idle",
      },
    ],
    icons: {
      "16": "icons/icon-16.png",
      "32": "icons/icon-32.png",
      "48": "icons/icon-48.png",
      "128": "icons/icon-128.png",
    },
    theme_color: template.themeColor,
    content_security_policy: {
      extension_pages: "script-src 'self'; object-src 'self'",
    },
  };
}
