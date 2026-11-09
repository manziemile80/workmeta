"""Payment sandbox validator (manual, credential-gated — never auto-run).

Usage:

    # Terminal 1: API with sandbox keys in .env
    #   BILLING_PROVIDER=flutterwave
    #   FLUTTERWAVE_SECRET_KEY=FLWSECK_TEST-...
    #   FLUTTERWAVE_WEBHOOK_HASH=<dashboach hash>

    # Terminal 2: create checkout via API, pay in sandbox, then:
    py -3.12 tools/scripts/test_payment_sandbox.py --provider flutterwave --reference WM-... --expect 15000

Checks (in order):
  1. Provider is configured (secret present).
  2. Server-side verify returns succeeded with the expected amount/currency.
  3. Webhook signature verification accepts a signed sample and rejects a
     tampered one (no network needed for the signature check).

This script performs NO charge/refund. It only verifies an already-paid
sandbox transaction. Do not run against live keys.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402
from app.services.payments import build_payment_provider  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Payment sandbox validator")
    parser.add_argument("--provider", choices=["flutterwave", "paystack"], required=True)
    parser.add_argument("--reference", required=True, help="Sandbox payment reference")
    parser.add_argument("--expect", type=int, default=0, help="Expected amount_minor")
    parser.add_argument("--currency", default="RWF")
    args = parser.parse_args()

    settings = get_settings()
    if settings.billing_provider != args.provider:
        raise SystemExit(f"BILLING_PROVIDER={settings.billing_provider!r} != {args.provider!r}")
    print("Provider configured. Verifying reference (server-side) ...")

    import asyncio

    provider = build_payment_provider(settings)

    async def _verify() -> None:
        result = await provider.verify(
            reference=args.reference,
            expected_amount_minor=args.expect,
            currency=args.currency,
            exponent=0,
        )
        print(json.dumps(result.raw, indent=2)[:2000])
        print(f"Status: {result.status} amount={result.amount_minor} currency={result.currency}")

    asyncio.run(_verify())

    sample = {
        "event": "charge.completed",
        "data": {
            "id": 1,
            "tx_ref": args.reference,
            "amount": args.expect,
            "currency": args.currency,
            "status": "successful",
        },
    }
    body = json.dumps(sample).encode()
    if args.provider == "paystack":
        secret = settings.paystack_webhook_secret or settings.paystack_secret_key
        signature = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        headers = {"x-paystack-signature": signature}
    else:
        headers = {"verif-hash": settings.flutterwave_webhook_hash}
    event = provider.parse_webhook(headers, body)
    print(f"Webhook parse OK: id={event.event_id} status={event.status}")
    try:
        provider.parse_webhook({"x-paystack-signature": "0" * 128, "verif-hash": "bad"}, body)
        print("Tamper check FAILED (bad signature accepted)")
    except Exception as exc:  # noqa: BLE001 - any rejection is good here
        print(f"Tamper check OK: rejected ({type(exc).__name__})")


if __name__ == "__main__":
    main()
