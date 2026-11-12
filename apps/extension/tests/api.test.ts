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
    payload: { id: "ext-1" },
  }));
  client.setToken("secret-token");

  await client.guidance("org-1", "ext-1", "How do I issue a receipt?");

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
    () => client.recognize("org-1", "https://ebm.example.com/"),
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

test("a recognition request carries the page address", async () => {
  const { calls, client } = stubFetch(() => ({ status: 200, payload: [] }));

  await client.recognize("org-1", "https://ebm.example.com/receipts?a=1");

  assert.match(
    calls[0].url,
    /\/organizations\/org-1\/extensions\/recognize\?url=https%3A%2F%2Febm\.example\.com%2Freceipts%3Fa%3D1$/,
  );
});

test("a guidance request sends the question and the page context", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: { answer: "Open the sales screen." },
  }));

  await client.guidance("org-1", "ext-1", "How do I issue a receipt?", {
    url: "https://ebm.example.com/",
    text: "Sales screen",
  });

  const body = JSON.parse(calls[0].body ?? "{}");
  assert.equal(body.question, "How do I issue a receipt?");
  assert.equal(body.context.url, "https://ebm.example.com/");
  assert.equal(body.context.text, "Sales screen");
});

test("a confirmation is recorded with the user's answer", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: { id: "plan-1", status: "confirmed" },
  }));

  await client.confirmAction("org-1", "ext-1", "plan-1", false);

  assert.equal(
    calls[0].url,
    "https://api.example.com/api/v1/organizations/org-1/extensions/ext-1/actions/plan-1/confirm",
  );
  assert.deepEqual(JSON.parse(calls[0].body ?? "{}"), { confirmed: false });
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
      () => client.recognize("org-1", "https://ebm.example.com/"),
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

test("the submodels of an organization are listed", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: [{ id: "sub-1", name: "EBM receipts", origin: "imported" }],
  }));

  const submodels = await client.submodels("org-1");

  assert.equal(
    calls[0].url,
    "https://api.example.com/api/v1/organizations/org-1/submodels",
  );
  assert.equal(calls[0].method, "GET");
  assert.equal(submodels[0].origin, "imported");
});

test("a submodel is asked directly, without matching the page", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: { answer: "Open the sales screen." },
  }));

  await client.submodelGuidance("org-1", "sub-1", "How do I issue a receipt?");

  assert.equal(
    calls[0].url,
    "https://api.example.com/api/v1/organizations/org-1/submodels/sub-1/guidance",
  );
  const body = JSON.parse(calls[0].body ?? "{}");
  assert.equal(body.question, "How do I issue a receipt?");
});

test("a submodel action is proposed, confirmed and completed", async () => {
  const { calls, client } = stubFetch(() => ({
    status: 200,
    payload: { id: "plan-1", status: "proposed" },
  }));

  await client.submodelProposeAction(
    "org-1",
    "sub-1",
    "payment",
    "Collect the invoice payment",
  );
  await client.submodelConfirmAction("org-1", "sub-1", "plan-1", true);
  await client.submodelCompleteAction("org-1", "sub-1", "plan-1", true, {
    receipt: "R-1",
  });

  assert.equal(
    calls[0].url,
    "https://api.example.com/api/v1/organizations/org-1/submodels/sub-1/actions",
  );
  assert.equal(
    calls[1].url,
    "https://api.example.com/api/v1/organizations/org-1/submodels/sub-1/actions/plan-1/confirm",
  );
  assert.deepEqual(JSON.parse(calls[1].body ?? "{}"), { confirmed: true });
  assert.equal(
    calls[2].url,
    "https://api.example.com/api/v1/organizations/org-1/submodels/sub-1/actions/plan-1/complete",
  );
  assert.deepEqual(JSON.parse(calls[2].body ?? "{}"), {
    succeeded: true,
    result: { receipt: "R-1" },
  });
});
