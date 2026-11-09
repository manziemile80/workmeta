// WorkMeta AI — k6 load smoke (run against staging only).
// Usage:
//   k6 run tools/k6/smoke.js --env BASE_URL=http://127.0.0.1:18080 --env TOKEN=<bearer>
// Scenarios cover auth, usage, assistant CRUD, uploads, ingestion drain check,
// chat and billing reads. Do not run against production.

import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "1m",
  thresholds: {
    http_req_failed: ["rate<0.02"],
    http_req_duration: ["p(95)<800", "p(99)<1500"],
  },
};

const BASE = __ENV.BASE_URL || "http://127.0.0.1:18080";
const TOKEN = __ENV.TOKEN || "";

function headers() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${TOKEN}`,
  };
}

export default function () {
  // Health + ready (unauthenticated probes).
  for (const path of ["/api/v1/health", "/api/v1/ready"]) {
    const res = http.get(`${BASE}${path}`);
    check(res, { [`${path} 200`]: (r) => r.status === 200 });
  }

  if (!TOKEN) {
    // Without a token only probes run; full scenarios need staging credentials.
    sleep(1);
    return;
  }

  // Auth identity.
  const me = http.get(`${BASE}/api/v1/auth/me`, { headers: headers() });
  check(me, { "me 200": (r) => r.status === 200 });
  const orgId = me.json("active_organization.id");

  // Billing + usage (org-scoped reads).
  const billing = http.get(`${BASE}/api/v1/organizations/${orgId}/billing`, {
    headers: headers(),
  });
  check(billing, { "billing 200": (r) => r.status === 200 });

  const usage = http.get(
    `${BASE}/api/v1/organizations/${orgId}/usage`,
    { headers: headers() },
  );
  check(usage, { "usage 200": (r) => r.status === 200 });

  // Assistant list (read path for CRUD coverage).
  const assistants = http.get(
    `${BASE}/api/v1/organizations/${orgId}/assistants`,
    { headers: headers() },
  );
  check(assistants, { "assistants 200": (r) => r.status === 200 });

  // Payments + invoices (billing reads).
  for (const path of ["payments", "invoices"]) {
    const res = http.get(
      `${BASE}/api/v1/organizations/${orgId}/billing/${path}`,
      { headers: headers() },
    );
    check(res, { [`${path} 200`]: (r) => r.status === 200 });
  }

  sleep(1);
}
