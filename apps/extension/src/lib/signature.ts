/**
 * Verifying the signed package.
 *
 * The server signs the canonical JSON of the manifest with Ed25519. The agent
 * verifies that signature in the browser with WebCrypto, so a client can prove
 * the package it runs is the one the platform published.
 *
 * Two details matter:
 *
 * 1. The signature covers the *canonical* bytes, so the agent verifies the
 *    bytes the server sent (`canonical_manifest`) instead of re-serialising
 *    the parsed manifest in JavaScript. Re-serialising would produce
 *    different bytes for the same value (number formatting, escaping), and
 *    the signature would then never match.
 * 2. The public key travels with the package, so the agent checks the key id
 *    it was built against. A package signed by a different key is refused
 *    rather than trusted silently.
 */

export interface SignatureCheck {
  valid: boolean;
  /** Why the signature was refused, empty when it was accepted. */
  reason: string;
  /** A short fingerprint of the key, so a human can compare it. */
  fingerprint: string;
}

export interface VerifyOptions {
  /** The exact text the signature covers. */
  canonicalManifest: string;
  /** Base64 Ed25519 signature. */
  signature: string;
  /** Base64 SPKI public key, as the API returns it. */
  publicKey: string;
  /** The key id this agent was built against, when it knows one. */
  expectedKeyId?: string | null;
  /** The key id the package claims to be signed with. */
  signingKeyId?: string | null;
}

/**
 * The bytes a signature covers.
 *
 * `Uint8Array` is what the agent reads and signs. The generic
 * `ArrayBufferView` is accepted so callers do not have to care about the
 * buffer type their reader produced.
 */
export type SignaturePayload = ArrayBufferView;

function decodeBase64(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

/** A short, stable fingerprint of a public key, for out-of-band comparison. */
export function keyFingerprint(publicKeyBase64: string): string {
  try {
    const bytes = decodeBase64(publicKeyBase64);
    let hash = 0x811c9dc5;
    for (const byte of bytes) {
      hash ^= byte;
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash.toString(16).padStart(8, "0");
  } catch {
    return "";
  }
}

/**
 * Check a signature over arbitrary bytes.
 *
 * This is the check the agent uses on the configuration file the packager
 * wrote: the bytes are verified exactly as they were read, so a change to
 * the file is detected.
 */
export async function verifySignedBytes(
  payload: SignaturePayload,
  signatureBase64: string,
  publicKeyBase64: string,
): Promise<SignatureCheck> {
  if (!signatureBase64 || !publicKeyBase64) {
    return {
      valid: false,
      reason: "There is no signature to check.",
      fingerprint: "",
    };
  }

  let key: CryptoKey;
  try {
    key = await crypto.subtle.importKey(
      "spki",
      decodeBase64(publicKeyBase64),
      { name: "Ed25519" },
      false,
      ["verify"],
    );
  } catch {
    return {
      valid: false,
      reason: "The signing key could not be read.",
      fingerprint: "",
    };
  }

  try {
    const ok = await crypto.subtle.verify(
      { name: "Ed25519" },
      key,
      decodeBase64(signatureBase64),
      payload as unknown as BufferSource,
    );
    if (!ok) {
      return {
        valid: false,
        reason:
          "The signature does not match the contents, so the file was " +
          "changed after it was signed.",
        fingerprint: keyFingerprint(publicKeyBase64),
      };
    }
    return {
      valid: true,
      reason: "",
      fingerprint: keyFingerprint(publicKeyBase64),
    };
  } catch {
    return {
      valid: false,
      reason: "The signature could not be checked.",
      fingerprint: keyFingerprint(publicKeyBase64),
    };
  }
}

/**
 * Check the package signature.
 *
 * A missing signature is reported as invalid with a clear reason: an unsigned
 * package is not a signed one, and the agent must not pretend otherwise.
 */
export async function verifyPackage(
  options: VerifyOptions,
): Promise<SignatureCheck> {
  const {
    canonicalManifest,
    signature,
    publicKey,
    expectedKeyId,
    signingKeyId,
  } = options;

  if (!signature || !publicKey) {
    return {
      valid: false,
      reason: "This package is not signed, so it cannot be verified.",
      fingerprint: "",
    };
  }

  if (expectedKeyId) {
    // The agent pins the key it trusts. A package that does not say which
    // key signed it is not accepted, because it cannot be shown to be the
    // key the agent expects.
    if (!signingKeyId) {
      return {
        valid: false,
        reason:
          "This package does not say which key signed it, so it cannot be " +
          `checked against "${expectedKeyId}".`,
        fingerprint: keyFingerprint(publicKey),
      };
    }
    if (expectedKeyId !== signingKeyId) {
      return {
        valid: false,
        reason:
          `This package is signed with key "${signingKeyId}", ` +
          `but this agent trusts "${expectedKeyId}".`,
        fingerprint: keyFingerprint(publicKey),
      };
    }
  }

  const check = await verifySignedBytes(
    new TextEncoder().encode(canonicalManifest),
    signature,
    publicKey,
  );
  if (!check.valid) {
    return {
      valid: false,
      reason:
        "The package signature does not match its contents, so the package " +
        "was changed after it was published.",
      fingerprint: check.fingerprint,
    };
  }
  return {
    valid: true,
    reason: "",
    fingerprint: check.fingerprint,
  };
}
