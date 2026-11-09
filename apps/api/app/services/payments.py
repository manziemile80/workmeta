"""Payment-provider abstraction for subscription billing.

The platform never stores card numbers or provider credentials in the database.
Only a provider reference, amount, currency and non-sensitive metadata are kept,
so a compromise of the application database does not expose payment instruments.

Supported providers
--------------------
``mock``
    Deterministic offline provider for development and tests. It signs webhooks
    with HMAC-SHA256. It must never be used in production.

``flutterwave``
    Rwanda-friendly gateway. Checkout is a redirect link and webhooks carry a
    shared ``verif-hash`` header. Amounts are major currency units.

``paystack``
    Checkout is a redirect link and webhooks are signed with HMAC-SHA512 over
    the raw request body. Amounts are the currency's minor unit.

No integration here should be described as production-ready until it has been
exercised against the provider's sandbox with real credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx
import structlog

from app.core.config import Settings, get_settings
from app.core.security import constant_time_equals
from app.models.enums import PaymentStatus

logger = structlog.get_logger(__name__)


class PaymentProviderError(Exception):
    """Raised when a payment provider cannot complete an operation."""


class WebhookVerificationError(Exception):
    """Raised when a webhook signature is missing or does not verify."""


@dataclass(slots=True)
class CheckoutSession:
    provider: str
    reference: str
    checkout_url: str | None


@dataclass(slots=True)
class PaymentVerification:
    reference: str
    status: str
    amount_minor: int | None = None
    currency: str | None = None
    provider_transaction_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RefundResult:
    reference: str
    status: str
    provider_refund_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookEvent:
    event_id: str
    event_type: str
    reference: str | None
    status: str
    amount_minor: int | None = None
    currency: str | None = None
    provider_transaction_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def amount_to_minor(amount: Any, exponent: int) -> int:
    """Convert a provider amount in major units to integer minor units."""
    try:
        value = Decimal(str(amount))
    except Exception as exc:  # noqa: BLE001 - provider payload is untrusted
        raise PaymentProviderError("Provider returned an invalid amount.") from exc
    scale = Decimal(10) ** exponent
    minor = (value * scale).to_integral_value()
    return int(minor)


def amount_to_major(amount_minor: int, exponent: int) -> float:
    scale = Decimal(10) ** exponent
    return float(Decimal(amount_minor) / scale)


def _json_body(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookVerificationError("The webhook body is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise WebhookVerificationError("The webhook body must be an object.")
    return value


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        customer_email: str,
        customer_name: str,
        description: str,
        metadata: dict[str, Any],
    ) -> CheckoutSession: ...

    async def verify(
        self,
        *,
        reference: str,
        expected_amount_minor: int,
        currency: str,
        exponent: int,
    ) -> PaymentVerification: ...

    async def refund(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        provider_transaction_id: str | None = None,
    ) -> RefundResult: ...

    def parse_webhook(
        self, headers: Mapping[str, str], body: bytes
    ) -> WebhookEvent: ...


class _HttpProvider:
    """Shared HTTP plumbing with a short timeout and no retry side effects."""

    def __init__(self, *, base_url: str, secret_key: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret_key = secret_key
        self.timeout_seconds = timeout_seconds

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise PaymentProviderError(
                "The payment provider is temporarily unreachable."
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise PaymentProviderError(
                "The payment provider returned an unreadable response."
            ) from exc
        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise PaymentProviderError(
                message or f"Payment provider error ({response.status_code})."
            )
        if not isinstance(payload, dict):
            raise PaymentProviderError("The payment provider returned an invalid payload.")
        return payload


class MockPaymentProvider:
    """Offline provider used by development and tests.

    Verification is deterministic: references prefixed with ``MOCKFAIL`` are
    treated as failed, everything else as successful. Tests should exercise the
    real webhook path instead of relying on this behaviour for production logic.
    """

    name = "mock"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def create_checkout(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        customer_email: str,
        customer_name: str,
        description: str,
        metadata: dict[str, Any],
    ) -> CheckoutSession:
        callback = self.settings.billing_checkout_callback_url
        separator = "&" if "?" in callback else "?"
        url = (
            f"{callback}{separator}provider=mock&reference={reference}"
            f"&amount={amount_to_major(amount_minor, exponent)}&currency={currency}"
        )
        return CheckoutSession(
            provider=self.name, reference=reference, checkout_url=url
        )

    async def verify(
        self,
        *,
        reference: str,
        expected_amount_minor: int,
        currency: str,
        exponent: int,
    ) -> PaymentVerification:
        failed = reference.upper().startswith("MOCKFAIL")
        return PaymentVerification(
            reference=reference,
            status=(
                PaymentStatus.failed.value
                if failed
                else PaymentStatus.succeeded.value
            ),
            amount_minor=expected_amount_minor,
            currency=currency,
            provider_transaction_id=f"mock-tx-{reference}",
            raw={"provider": "mock"},
        )

    async def refund(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        provider_transaction_id: str | None = None,
    ) -> RefundResult:
        return RefundResult(
            reference=reference,
            status=PaymentStatus.succeeded.value,
            provider_refund_id=f"mock-refund-{reference}",
            raw={"provider": "mock"},
        )

    def parse_webhook(
        self, headers: Mapping[str, str], body: bytes
    ) -> WebhookEvent:
        signature = headers.get("x-mock-signature", "")
        expected = hmac.new(
            self.settings.billing_mock_webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not signature or not constant_time_equals(signature, expected):
            raise WebhookVerificationError("The mock webhook signature is invalid.")

        payload = _json_body(body)
        event_id = payload.get("event_id")
        event_type = payload.get("type")
        data = payload.get("data")
        if not isinstance(event_id, str) or not event_id:
            raise WebhookVerificationError("The webhook event is missing an id.")
        if not isinstance(event_type, str) or not event_type:
            raise WebhookVerificationError("The webhook event is missing a type.")
        if not isinstance(data, dict):
            data = {}

        if event_type == "refund.succeeded":
            status = PaymentStatus.refunded.value
        elif event_type == "payment.failed":
            status = PaymentStatus.failed.value
        else:
            status = PaymentStatus.succeeded.value

        return WebhookEvent(
            event_id=event_id,
            event_type=event_type,
            reference=data.get("reference"),
            status=status,
            amount_minor=data.get("amount_minor"),
            currency=data.get("currency"),
            provider_transaction_id=data.get("transaction_id"),
            raw=payload,
        )


class FlutterwavePaymentProvider(_HttpProvider):
    name = "flutterwave"

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.flutterwave_base_url,
            secret_key=settings.flutterwave_secret_key,
            timeout_seconds=20.0,
        )
        self.webhook_hash = settings.flutterwave_webhook_hash

    async def create_checkout(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        customer_email: str,
        customer_name: str,
        description: str,
        metadata: dict[str, Any],
    ) -> CheckoutSession:
        payload = await self._request(
            "POST",
            "/payments",
            json_body={
                "tx_ref": reference,
                "amount": amount_to_major(amount_minor, exponent),
                "currency": currency,
                "redirect_url": self.settings.billing_checkout_callback_url,
                "customer": {"email": customer_email, "name": customer_name},
                "customizations": {"title": description},
                "meta": metadata,
            },
        )
        data = payload.get("data")
        if payload.get("status") != "success" or not isinstance(data, dict):
            raise PaymentProviderError("Flutterwave did not create a checkout session.")
        return CheckoutSession(
            provider=self.name,
            reference=reference,
            checkout_url=data.get("link"),
        )

    async def verify(
        self,
        *,
        reference: str,
        expected_amount_minor: int,
        currency: str,
        exponent: int,
    ) -> PaymentVerification:
        payload = await self._request(
            "GET", f"/transactions/verify_by_reference?tx_ref={reference}"
        )
        data = payload.get("data")
        if payload.get("status") != "success" or not isinstance(data, dict):
            raise PaymentProviderError("Flutterwave could not verify the payment.")
        provider_status = str(data.get("status", "")).lower()
        status = (
            PaymentStatus.succeeded.value
            if provider_status == "successful"
            else PaymentStatus.failed.value
        )
        return PaymentVerification(
            reference=reference,
            status=status,
            amount_minor=amount_to_minor(data.get("amount"), exponent),
            currency=str(data.get("currency") or currency),
            provider_transaction_id=(
                str(data.get("id")) if data.get("id") is not None else None
            ),
            raw=data,
        )

    async def refund(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        provider_transaction_id: str | None = None,
    ) -> RefundResult:
        transaction_id = provider_transaction_id
        if not transaction_id:
            verification = await self.verify(
                reference=reference,
                expected_amount_minor=amount_minor,
                currency=currency,
                exponent=exponent,
            )
            transaction_id = verification.provider_transaction_id
        if not transaction_id:
            raise PaymentProviderError("Flutterwave did not return a transaction id.")
        payload = await self._request(
            "POST",
            f"/transactions/{transaction_id}/refund",
            json_body={"amount": amount_to_major(amount_minor, exponent)},
        )
        data = payload.get("data")
        if payload.get("status") != "success":
            raise PaymentProviderError("Flutterwave did not accept the refund.")
        return RefundResult(
            reference=reference,
            status=PaymentStatus.succeeded.value,
            provider_refund_id=(
                str(data.get("id")) if isinstance(data, dict) and data.get("id") else None
            ),
            raw=data if isinstance(data, dict) else {},
        )

    def parse_webhook(
        self, headers: Mapping[str, str], body: bytes
    ) -> WebhookEvent:
        supplied = headers.get("verif-hash", "")
        if not self.webhook_hash or not supplied or not constant_time_equals(
            supplied, self.webhook_hash
        ):
            raise WebhookVerificationError(
                "The Flutterwave webhook signature is invalid."
            )

        payload = _json_body(body)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise WebhookVerificationError("The webhook is missing its data object.")
        event_type = str(payload.get("event") or payload.get("event.type") or "unknown")
        transaction_id = data.get("id")
        event_id = (
            f"{event_type}:{transaction_id}"
            if transaction_id is not None
            else hashlib.sha256(body).hexdigest()
        )
        provider_status = str(data.get("status", "")).lower()
        if event_type.startswith("refund"):
            status = PaymentStatus.refunded.value
        elif provider_status in {"successful", "success"}:
            status = PaymentStatus.succeeded.value
        elif provider_status in {"failed", "cancelled", "canceled"}:
            status = PaymentStatus.failed.value
        else:
            status = PaymentStatus.pending.value
        return WebhookEvent(
            event_id=event_id,
            event_type=event_type,
            reference=data.get("tx_ref"),
            status=status,
            amount_minor=(
                amount_to_minor(data.get("amount"), 0)
                if data.get("amount") is not None
                else None
            ),
            currency=str(data.get("currency")) if data.get("currency") else None,
            provider_transaction_id=str(transaction_id) if transaction_id else None,
            raw=payload,
        )


class PaystackPaymentProvider(_HttpProvider):
    name = "paystack"

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            base_url=settings.paystack_base_url,
            secret_key=settings.paystack_secret_key,
            timeout_seconds=20.0,
        )
        self.webhook_secret = (
            settings.paystack_webhook_secret or settings.paystack_secret_key
        )

    async def create_checkout(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        customer_email: str,
        customer_name: str,
        description: str,
        metadata: dict[str, Any],
    ) -> CheckoutSession:
        payload = await self._request(
            "POST",
            "/transaction/initialize",
            json_body={
                "email": customer_email,
                "amount": amount_minor,
                "currency": currency,
                "reference": reference,
                "callback_url": self.settings.billing_checkout_callback_url,
                "metadata": {**metadata, "customer_name": customer_name},
            },
        )
        data = payload.get("data")
        if payload.get("status") is not True or not isinstance(data, dict):
            raise PaymentProviderError("Paystack did not create a checkout session.")
        return CheckoutSession(
            provider=self.name,
            reference=reference,
            checkout_url=data.get("authorization_url"),
        )

    async def verify(
        self,
        *,
        reference: str,
        expected_amount_minor: int,
        currency: str,
        exponent: int,
    ) -> PaymentVerification:
        payload = await self._request("GET", f"/transaction/verify/{reference}")
        data = payload.get("data")
        if payload.get("status") is not True or not isinstance(data, dict):
            raise PaymentProviderError("Paystack could not verify the payment.")
        status = (
            PaymentStatus.succeeded.value
            if str(data.get("status", "")).lower() == "success"
            else PaymentStatus.failed.value
        )
        return PaymentVerification(
            reference=reference,
            status=status,
            amount_minor=(
                int(data["amount"]) if data.get("amount") is not None else None
            ),
            currency=str(data.get("currency") or currency),
            provider_transaction_id=(
                str(data.get("id")) if data.get("id") is not None else None
            ),
            raw=data,
        )

    async def refund(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        exponent: int,
        provider_transaction_id: str | None = None,
    ) -> RefundResult:
        payload = await self._request(
            "POST",
            "/refund",
            json_body={
                "transaction": provider_transaction_id or reference,
                "amount": amount_minor,
            },
        )
        data = payload.get("data")
        if payload.get("status") is not True:
            raise PaymentProviderError("Paystack did not accept the refund.")
        return RefundResult(
            reference=reference,
            status=PaymentStatus.succeeded.value,
            provider_refund_id=(
                str(data.get("id")) if isinstance(data, dict) and data.get("id") else None
            ),
            raw=data if isinstance(data, dict) else {},
        )

    def parse_webhook(
        self, headers: Mapping[str, str], body: bytes
    ) -> WebhookEvent:
        signature = headers.get("x-paystack-signature", "")
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"), body, hashlib.sha512
        ).hexdigest()
        if not self.webhook_secret or not signature or not constant_time_equals(
            signature, expected
        ):
            raise WebhookVerificationError("The Paystack webhook signature is invalid.")

        payload = _json_body(body)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise WebhookVerificationError("The webhook is missing its data object.")
        event_type = str(payload.get("event") or "unknown")
        transaction_id = data.get("id")
        event_id = (
            str(transaction_id)
            if transaction_id is not None
            else hashlib.sha256(body).hexdigest()
        )
        if event_type.startswith("refund"):
            status = PaymentStatus.refunded.value
        elif str(data.get("status", "")).lower() == "success":
            status = PaymentStatus.succeeded.value
        elif str(data.get("status", "")).lower() in {"failed", "abandoned"}:
            status = PaymentStatus.failed.value
        else:
            status = PaymentStatus.pending.value
        return WebhookEvent(
            event_id=event_id,
            event_type=event_type,
            reference=data.get("reference"),
            status=status,
            amount_minor=(
                int(data["amount"]) if data.get("amount") is not None else None
            ),
            currency=str(data.get("currency")) if data.get("currency") else None,
            provider_transaction_id=str(transaction_id) if transaction_id else None,
            raw=payload,
        )


def build_payment_provider(settings: Settings | None = None) -> PaymentProvider:
    settings = settings or get_settings()
    if settings.billing_provider == "mock":
        if settings.is_production:
            raise RuntimeError(
                "BILLING_PROVIDER=mock is not allowed in production. "
                "Configure Flutterwave or Paystack."
            )
        return MockPaymentProvider(settings)
    if settings.billing_provider == "flutterwave":
        if not settings.flutterwave_secret_key:
            raise RuntimeError("FLUTTERWAVE_SECRET_KEY is required.")
        return FlutterwavePaymentProvider(settings)
    if settings.billing_provider == "paystack":
        if not settings.paystack_secret_key:
            raise RuntimeError("PAYSTACK_SECRET_KEY is required.")
        return PaystackPaymentProvider(settings)
    raise RuntimeError(f"Unsupported billing provider: {settings.billing_provider!r}")


def get_payment_provider() -> PaymentProvider:
    """FastAPI dependency. Tests can override this with the mock provider."""
    return build_payment_provider()
