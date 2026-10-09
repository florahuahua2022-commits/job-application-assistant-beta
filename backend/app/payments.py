from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .config import settings


STRIPE_API_VERSION = "2026-09-30.endive"
CATALOG_VERSION = "2026-10-09"

_BASE_PACKAGES = {
    "single": (1, 1695),
    "starter": (8, 10995),
    "job_search": (18, 19900),
}


@dataclass(frozen=True)
class PaymentPackage:
    code: str
    credits: int
    subtotal_cents: int
    gst_cents: int
    total_cents: int
    currency: str
    single_pack_price_cents: int
    catalog_version: str


def _gst_cents(subtotal_cents: int, enabled: bool) -> int:
    if not enabled:
        return 0
    return int((Decimal(subtotal_cents) * Decimal("0.10")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def build_pack_catalog(gst_enabled: bool) -> dict[str, PaymentPackage]:
    single_gst = _gst_cents(_BASE_PACKAGES["single"][1], gst_enabled)
    single_total = _BASE_PACKAGES["single"][1] + single_gst
    return {
        code: PaymentPackage(
            code=code,
            credits=credits,
            subtotal_cents=subtotal,
            gst_cents=(gst := _gst_cents(subtotal, gst_enabled)),
            total_cents=subtotal + gst,
            currency="AUD",
            single_pack_price_cents=single_total,
            catalog_version=CATALOG_VERSION,
        )
        for code, (credits, subtotal) in _BASE_PACKAGES.items()
    }


def purchasable_package(package_code: str, gst_enabled: bool | None = None) -> PaymentPackage:
    catalog = build_pack_catalog(settings.stripe_gst_enabled if gst_enabled is None else gst_enabled)
    try:
        return catalog[package_code]
    except KeyError as error:
        raise ValueError("Unknown purchasable package") from error


def validate_stripe_settings(configured=settings) -> None:
    mode = configured.stripe_mode.strip().lower()
    if mode not in {"test", "live"}:
        raise ValueError("STRIPE_MODE must be test or live")
    key = configured.stripe_secret_key
    if not key:
        return
    if not configured.stripe_webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is required when Stripe is enabled")
    expected_prefix = "sk_live_" if mode == "live" else "sk_test_"
    if not key.startswith(expected_prefix):
        raise ValueError("Stripe key does not match STRIPE_MODE")
