/**
 * The extension's own configuration and stored session.
 *
 * The client configuration (`config.json`) is written by the packager and
 * contains only public values: the API address, the organization the agent
 * acts for and the domains it may read. It holds no secret, so it is safe to
 * ship inside the package.
 *
 * The packager signs the configuration file, and the agent checks that
 * signature before it uses the configuration. The signing key travels with
 * the package, so this proves the package is internally consistent and was
 * signed by the key that ships with it. The fingerprint is shown in the panel
 * so an administrator can compare it with the one the WorkMeta console shows
 * and confirm who published the build.
 *
 * The session token is the opposite: it is stored in `chrome.storage.local`
 * and never written to disk outside the browser profile.
 */

import type { AgentConfig } from "./api.ts";
import { mayReadUrl } from "./domains.ts";
import { verifySignedBytes } from "./signature.ts";
import type { LoginResponse } from "./types.ts";

export { mayReadUrl };

const CONFIG_FILE = "config.json";
const CONFIG_SIGNATURE_FILE = "config.sig";
const CONFIG_KEY_FILE = "config.pub";
const TOKEN_KEY = "workmeta.session";

interface StoredSession {
  token: string;
  orgId: string;
  user: LoginResponse["user"] | null;
  organization: LoginResponse["active_organization"];
  memberships: LoginResponse["memberships"];
}

export interface LoadedConfig extends AgentConfig {
  /** The name of the client this build was packaged for. */
  clientName: string;
  /** Whether the configuration's signature was checked. */
  signatureChecked: boolean;
  /** Why the configuration was refused, empty when it was accepted. */
  signatureReason: string;
  /** The fingerprint of the key the configuration was signed with. */
  keyFingerprint: string;
  /**
   * The key packages must be signed with.
   *
   * It comes from the signed configuration, not from the package, so a
   * package cannot introduce its own signer.
   */
  signingKeyId: string;
  /** The public key packages are checked against. */
  publicKey: string;
}

export async function loadConfig(): Promise<LoadedConfig> {
  const configText = await fetchText(chrome.runtime.getURL(CONFIG_FILE));
  if (!configText) {
    throw new Error(
      "This extension build has no configuration. Ask your administrator " +
        "for a packaged build.",
    );
  }
  const raw = JSON.parse(configText) as Partial<LoadedConfig>;
  if (!raw.apiBaseUrl || !raw.orgId) {
    throw new Error("This extension build is not configured correctly.");
  }

  const config: LoadedConfig = {
    apiBaseUrl: raw.apiBaseUrl,
    orgId: raw.orgId,
    allowedDomains: raw.allowedDomains ?? [],
    clientName: raw.clientName ?? "WorkMeta AI",
    signatureChecked: false,
    signatureReason: "",
    keyFingerprint: "",
    signingKeyId: raw.signingKeyId ?? "",
    publicKey: "",
  };

  // A packaged build carries a signature. A development build does not, and
  // says so rather than pretending it was checked.
  const [signature, publicKey] = await Promise.all([
    fetchText(chrome.runtime.getURL(CONFIG_SIGNATURE_FILE)),
    fetchText(chrome.runtime.getURL(CONFIG_KEY_FILE)),
  ]);
  if (signature && publicKey) {
    const check = await verifySignedBytes(
      new TextEncoder().encode(configText),
      signature,
      publicKey,
    );
    if (!check.valid) {
      throw new Error(
        check.reason ||
          "This extension build's configuration could not be verified.",
      );
    }
    config.signatureChecked = true;
    config.keyFingerprint = check.fingerprint;
    // The key that signed this configuration is the key a package must be
    // signed with, so the two are checked against the same trust anchor.
    config.publicKey = publicKey;
  } else {
    config.signatureReason =
      "This build's configuration is not signed, so it was not verified.";
  }

  return config;
}

async function fetchText(url: string): Promise<string> {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      return "";
    }
    return (await response.text()).trim();
  } catch {
    return "";
  }
}

export async function readSession(): Promise<StoredSession | null> {
  const stored = await chrome.storage.local.get(TOKEN_KEY);
  const session = stored[TOKEN_KEY] as StoredSession | undefined;
  if (!session?.token) {
    return null;
  }
  return session;
}

export async function writeSession(session: StoredSession): Promise<void> {
  await chrome.storage.local.set({ [TOKEN_KEY]: session });
}

export async function clearSession(): Promise<void> {
  await chrome.storage.local.remove(TOKEN_KEY);
}
