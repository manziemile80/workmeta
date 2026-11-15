/**
 * Tests for the agent's API client.
 *
 * The client is the only thing that talks to the server, so these tests
 * check the two things that matter: that the session token is attached, and
 * that the server's error envelope is turned into a readable error rather
 * than a silent failure.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { ApiError, WorkMetaClient } from "../src/lib/api.ts";

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | undefined;
}

function stubFetch(
  responder: (call: Call) => { status: number; payload: unknown },
): { calls: Call[]; client: WorkMetaClient } {
  const calls: Call[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: unknown, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === "string" ? init.body : undefined,
    };
    calls.push(call);
    const { status, payload } = responder(call);
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  void original;
  return { calls, client: new WorkMetaClient("https://api.example.com/api/v1") };
}

test("the session token is sent as a bearer token", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: { user: { id: "u1" } },
  }));
  client.setToken("secret-token");

  await client.me();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].headers.Authorization, "Bearer secret-token");
});

test("an extension login asks for a token, not a cookie", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: {
      user: { id: "u1" },
      active_organization: null,
      memberships: [],
      session: { kind: "extension", expires_at: "2026-12-01T00:00:00Z" },
      access_token: "token-1",
    },
  }));

  const response = await client.login("user@example.com", "password123");

  assert.equal(calls[0].url, "https://api.example.com/api/v1/auth/login");
  assert.equal(calls[0].method, "POST");
  const body = JSON.parse(calls[0].body ?? "{}");
  assert.equal(body.kind, "extension");
  assert.equal(response.access_token, "token-1");
  assert.equal(client.getToken(), "token-1");
});

test("the error envelope becomes a readable error", async () => {
  const { client } = stubFetch(() => ({
    status: 403,
    payload: {
      error: {
        code: "membership_required",
        message: "Organization membership is required.",
        request_id: "req-7",
      },
    },
  }));

  await assert.rejects(
    () => client.me(),
    (error: unknown) => {
      const failure = error as ApiError;
      assert.ok(failure instanceof ApiError);
      assert.equal(failure.status, 403);
      assert.equal(failure.code, "membership_required");
      assert.equal(failure.message, "Organization membership is required.");
      assert.equal(failure.requestId, "req-7");
      return true;
    },
  );
});

test("switching organization posts the chosen organization", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: { user: { id: "u1" }, active_organization: null, memberships: [] },
  }));
  client.setToken("secret-token");

  await client.switchOrganization("org-1");

  assert.equal(
    calls[0].url,
    "https://api.example.com/api/v1/auth/switch-organization",
  );
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(JSON.parse(calls[0].body ?? "{}"), { org_id: "org-1" });
});

test("signing out revokes the session on the server", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: { status: "signed_out" },
  }));
  client.setToken("secret-token");

  await client.logout();

  assert.equal(calls[0].url, "https://api.example.com/api/v1/auth/logout");
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].headers.Authorization, "Bearer secret-token");
});

test("signing out works even when the server cannot be reached", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error("offline");
  }) as typeof fetch;
  const client = new WorkMetaClient("https://api.example.com/api/v1");
  try {
    await client.logout();
  } finally {
    globalThis.fetch = original;
  }
});

test("a network failure is reported as such", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error("offline");
  }) as typeof fetch;
  const client = new WorkMetaClient("https://api.example.com/api/v1");
  try {
    await assert.rejects(
      () => client.me(),
      (error: unknown) => {
        const failure = error as ApiError;
        assert.ok(failure instanceof ApiError);
        assert.equal(failure.code, "network_unreachable");
        return true;
      },
    );
  } finally {
    globalThis.fetch = original;
  }
});
