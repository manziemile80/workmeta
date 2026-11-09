"""Validate a running WorkMeta AI staging deployment (no secrets printed).

Usage:
    py tools/scripts/validate_staging.py --base-url http://127.0.0.1:18080

Checks /health, /ready and /metrics through the edge proxy, then (with
--token) /auth/me. Exits non-zero if any check fails. Run after
`uvicorn`/`docker compose` is up; see docs/STAGING.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

OK = True


def _get(base: str, path: str, token: str | None = None) -> tuple[int, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except Exception as exc:  # noqa: BLE001 - validator must report, not crash
        return 0, str(exc)


def check(base: str, path: str, token: str | None = None) -> bool:
    global OK
    status, body = _get(base, path, token)
    ok = status == 200
    OK = OK and ok
    print(f"{'PASS' if ok else 'FAIL'}  {path} -> {status}")
    if not ok:
        print(body[:400])
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate staging deployment")
    parser.add_argument("--base-url", required=True, help="Edge base URL, e.g. http://127.0.0.1:18080")
    parser.add_argument("--token", default=None, help="Extension bearer token for /me")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    check(base, "/api/v1/health")
    check(base, "/api/v1/ready")
    status, body = _get(base, "/api/v1/metrics")
    metrics_ok = status == 200 and "workmeta_uptime_seconds" in body
    print(f"{'PASS' if metrics_ok else 'FAIL'}  /api/v1/metrics -> {status}")
    if metrics_ok:
        print(f"  queue line present: {'workmeta_ingestion_jobs' in body}")
    if args.token:
        check(base, "/api/v1/auth/me", args.token)
    else:
        print("SKIP  /api/v1/auth/me (no --token)")

    print(f"\n{'STAGING VALIDATION PASSED' if OK else 'STAGING VALIDATION FAILED'}")
    raise SystemExit(0 if OK else 1)


if __name__ == "__main__":
    main()
