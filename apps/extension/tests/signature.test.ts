/**
 * Tests for the signature checks.
 *
 * The agent verifies the signed package and the signed configuration with
 * WebCrypto. These tests use a real Ed25519 key, so they prove the same
 * code path the browser runs.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";

import { keyFingerprint, verifyPackage, verifySignedBytes } from "../src/lib/signature.ts";

// The browser's crypto is what the extension uses; in Node it comes from
// `node:crypto`.
Object.defineProperty(globalThis, "crypto", {
  value: webcrypto,
  configurable: true,
});

const encoder = new TextEncoder();

async function makeKeyPair(): Promise<{
  publicKey: string;
  privateKey: CryptoKey;
}> {
  const pair = (await webcrypto.subtle.generateKey(
    { name: "Ed25519" },
    true,
    ["sign", "verify"],
  )) as unknown as CryptoKeyPair;
  const spki = await webcrypto.subtle.exportKey("spki", pair.publicKey);
  return {
    publicKey: Buffer.from(spki).toString("base64"),
    privateKey: pair.privateKey,
  };
}

async function sign(
  privateKey: CryptoKey,
  payload: Uint8Array,
): Promise<string> {
  const raw = await webcrypto.subtle.sign(
    { name: "Ed25519" },
    privateKey,
    payload as unknown as BufferSource,
  );
  return Buffer.from(raw).toString("base64");
}

test("a signature over the canonical manifest is accepted", async () => {
  const { publicKey, privateKey } = await makeKeyPair();
  const manifest = '{"schema":"workmeta.extension/1","version":1}';
  const signature = await sign(privateKey, encoder.encode(manifest));

  const check = await verifyPackage({
    canonicalManifest: manifest,
    signature,
    publicKey,
    signingKeyId: "test-1",
  });

  assert.equal(check.valid, true);
  assert.equal(check.reason, "");
  assert.ok(check.fingerprint.length > 0);
});

test("a tampered manifest is refused", async () => {
  const { publicKey, privateKey } = await makeKeyPair();
  const manifest = '{"schema":"workmeta.extension/1","version":1}';
  const signature = await sign(privateKey, encoder.encode(manifest));

  const check = await verifyPackage({
    canonicalManifest: '{"schema":"workmeta.extension/1","version":2}',
    signature,
    publicKey,
    signingKeyId: "test-1",
  });

  assert.equal(check.valid, false);
  assert.match(check.reason, /does not match/);
});

test("an unsigned package is refused and says so", async () => {
  const { publicKey } = await makeKeyPair();
  const check = await verifyPackage({
    canonicalManifest: "{}",
    signature: "",
    publicKey,
  });
  assert.equal(check.valid, false);
  assert.match(check.reason, /not signed/);
});

test("a package signed by an untrusted key is refused", async () => {
  const { publicKey, privateKey } = await makeKeyPair();
  const manifest = "{}";
  const signature = await sign(privateKey, encoder.encode(manifest));

  const check = await verifyPackage({
    canonicalManifest: manifest,
    signature,
    publicKey,
    expectedKeyId: "trusted-1",
    signingKeyId: "other-1",
  });

  assert.equal(check.valid, false);
  assert.match(check.reason, /other-1/);
});

test("a package signed by the trusted key is accepted", async () => {
  const { publicKey, privateKey } = await makeKeyPair();
  const manifest = "{}";
  const signature = await sign(privateKey, encoder.encode(manifest));

  const check = await verifyPackage({
    canonicalManifest: manifest,
    signature,
    publicKey,
    expectedKeyId: "trusted-1",
    signingKeyId: "trusted-1",
  });

  assert.equal(check.valid, true);
});

test("a package that names no key is refused when one is expected", async () => {
  const { publicKey, privateKey } = await makeKeyPair();
  const manifest = "{}";
  const signature = await sign(privateKey, encoder.encode(manifest));

  // The agent pins a key, so a package that does not say which key signed it
  // is not accepted.
  const check = await verifyPackage({
    canonicalManifest: manifest,
    signature,
    publicKey,
    expectedKeyId: "trusted-1",
    signingKeyId: null,
  });

  assert.equal(check.valid, false);
  assert.match(check.reason, /trusted-1/);
});

test("signed bytes are verified exactly as they were read", async () => {
  const { publicKey, privateKey } = await makeKeyPair();
  const payload = encoder.encode('{"apiBaseUrl":"https://api.example.com"}');
  const signature = await sign(privateKey, payload);

  const good = await verifySignedBytes(payload, signature, publicKey);
  assert.equal(good.valid, true);

  const changed = await verifySignedBytes(
    encoder.encode('{"apiBaseUrl":"https://evil.example.com"}'),
    signature,
    publicKey,
  );
  assert.equal(changed.valid, false);
  assert.match(changed.reason, /changed after it was signed/);
});

test("a missing signature is refused", async () => {
  const { publicKey } = await makeKeyPair();
  const check = await verifySignedBytes(encoder.encode("x"), "", publicKey);
  assert.equal(check.valid, false);
});

test("the fingerprint is stable and differs between keys", async () => {
  const first = await makeKeyPair();
  const second = await makeKeyPair();
  assert.equal(
    keyFingerprint(first.publicKey),
    keyFingerprint(first.publicKey),
  );
  assert.notEqual(
    keyFingerprint(first.publicKey),
    keyFingerprint(second.publicKey),
  );
});
