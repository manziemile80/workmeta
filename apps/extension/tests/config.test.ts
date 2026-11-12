/**
 * Tests for the domain check.
 *
 * The agent must never read a page outside the systems the client trained.
 * This is the rule that keeps it from collecting what it was not asked for.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { mayReadUrl } from "../src/lib/domains.ts";

test("a trained domain may be read", () => {
  assert.equal(
    mayReadUrl("https://ebm.example.com/receipts/1", ["ebm.example.com"]),
    true,
  );
});

test("a different domain may not be read", () => {
  assert.equal(
    mayReadUrl("https://mail.example.com/inbox", ["ebm.example.com"]),
    false,
  );
});

test("a look-alike domain may not be read", () => {
  assert.equal(
    mayReadUrl("https://ebm.example.com.evil.test/", ["ebm.example.com"]),
    false,
  );
  assert.equal(
    mayReadUrl("https://notebm.example.com/", ["ebm.example.com"]),
    false,
  );
});

test("a wildcard covers the domain and its subdomains", () => {
  assert.equal(
    mayReadUrl("https://app.example.com/x", ["*.example.com"]),
    true,
  );
  assert.equal(
    mayReadUrl("https://example.com/x", ["*.example.com"]),
    true,
  );
  assert.equal(
    mayReadUrl("https://example.org/x", ["*.example.com"]),
    false,
  );
});

test("no allowed domains means nothing may be read", () => {
  assert.equal(mayReadUrl("https://ebm.example.com/", []), false);
});

test("a malformed address is refused", () => {
  assert.equal(mayReadUrl("not a url", ["ebm.example.com"]), false);
  assert.equal(mayReadUrl("", ["ebm.example.com"]), false);
});

test("the comparison ignores case", () => {
  assert.equal(
    mayReadUrl("https://EBM.Example.com/x", ["ebm.example.com"]),
    true,
  );
});
