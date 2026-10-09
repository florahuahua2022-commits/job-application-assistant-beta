import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.auth import get_authenticated_user
from app.config import Settings


class StripePaymentCatalogTests(unittest.TestCase):
    def test_gst_defaults_off_and_keeps_catalog_prices_unchanged(self):
        from app.payments import build_pack_catalog

        catalog = build_pack_catalog(gst_enabled=False)

        self.assertEqual(
            {
                code: (item.subtotal_cents, item.gst_cents, item.total_cents, item.single_pack_price_cents)
                for code, item in catalog.items()
            },
            {
                "single": (1695, 0, 1695, 1695),
                "starter": (10995, 0, 10995, 1695),
                "job_search": (19900, 0, 19900, 1695),
            },
        )

    def test_gst_enabled_rounds_once_per_order_half_up(self):
        from app.payments import build_pack_catalog

        catalog = build_pack_catalog(gst_enabled=True)

        self.assertEqual(
            {
                code: (item.gst_cents, item.total_cents, item.single_pack_price_cents)
                for code, item in catalog.items()
            },
            {
                "single": (170, 1865, 1865),
                "starter": (1100, 12095, 1865),
                "job_search": (1990, 21890, 1865),
            },
        )

    def test_custom_and_unknown_packages_are_not_purchasable(self):
        from app.payments import purchasable_package

        for package_code in ("custom", "unknown"):
            with self.subTest(package_code=package_code), self.assertRaises(ValueError):
                purchasable_package(package_code, gst_enabled=False)

    def test_stripe_settings_default_to_test_mode_with_gst_off(self):
        configured = Settings(_env_file=None)

        self.assertEqual(configured.stripe_mode, "test")
        self.assertFalse(configured.stripe_gst_enabled)

    def test_authenticated_identity_uses_verified_claim_email(self):
        user_id = uuid4()
        with patch("app.auth._verified_claims", return_value={
            "sub": str(user_id), "email": "verified@example.test", "role": "authenticated",
        }):
            identity = get_authenticated_user("Bearer signed-token")

        self.assertEqual((identity.id, identity.email), (user_id, "verified@example.test"))

    def test_stripe_settings_reject_mode_key_mismatch(self):
        from app.payments import validate_stripe_settings

        configured = Settings(
            _env_file=None,
            stripe_mode="live",
            stripe_secret_key="sk_test_fake",
            stripe_webhook_secret="whsec_fake",
        )
        with self.assertRaises(ValueError):
            validate_stripe_settings(configured)

    def test_stripe_settings_require_webhook_secret_when_key_is_configured(self):
        from app.payments import validate_stripe_settings

        configured = Settings(_env_file=None, stripe_secret_key="sk_test_fake")
        with self.assertRaises(ValueError):
            validate_stripe_settings(configured)


if __name__ == "__main__":
    unittest.main()
