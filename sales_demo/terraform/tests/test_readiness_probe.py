from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "readiness_probe.py"
SPEC = importlib.util.spec_from_file_location("readiness_probe", MODULE_PATH)
assert SPEC and SPEC.loader
readiness_probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = readiness_probe
SPEC.loader.exec_module(readiness_probe)


class ReadinessProbeTests(unittest.TestCase):
    def _responses(self):
        source = "a" * 40
        release = "b" * 64
        config = {
            "apiBaseUrl": "https://api123.execute-api.us-east-1.amazonaws.com",
            "awsRegion": "us-east-1",
            "cognitoUserPool": "us-east-1_Synthetic",
            "cognitoClientId": "synthetic-client",
            "cognitoDomain": "https://approvals-sales.auth.us-east-1.amazoncognito.com",
            "redirectUri": "https://example.cloudfront.net/auth/callback",
            "logoutUri": "https://example.cloudfront.net/",
            "oauthFlow": "authorization_code_pkce",
            "scopes": ["openid"],
            "expiresAt": None,
            "syntheticDataOnly": True,
            "deploymentBinding": {
                "sourceRevision": source,
                "frontendReleaseSha256": release,
            },
        }
        values = {
            "https://example.cloudfront.net/": readiness_probe.HttpResult(
                200, {"content-type": "text/html"}, b'<div id="root"></div>'
            ),
            "https://example.cloudfront.net/runtime-config.json": readiness_probe.HttpResult(
                200,
                {"content-type": "application/json", "cache-control": "no-store"},
                json.dumps(config).encode(),
            ),
            "https://api123.execute-api.us-east-1.amazonaws.com/health": readiness_probe.HttpResult(
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "status": "PREPARED",
                        "expiresAt": None,
                        "syntheticData": True,
                        "pii": False,
                    }
                ).encode(),
            ),
            "https://api123.execute-api.us-east-1.amazonaws.com/me": readiness_probe.HttpResult(
                401, {"content-type": "application/json"}, b"{}"
            ),
        }
        values[readiness_probe.authorization_probe_url(config)] = readiness_probe.HttpResult(
            200,
            {"content-type": "text/html;charset=UTF-8"},
            b"<html><body><form>Classic hosted login</form></body></html>",
        )
        return source, release, values

    def test_exact_prepared_public_contract_is_green(self) -> None:
        source, release, values = self._responses()
        result = readiness_probe.evaluate(
            web_url="https://example.cloudfront.net",
            expected_region="us-east-1",
            expected_source_revision=source,
            expected_release_sha256=release,
            fetcher=values.__getitem__,
        )
        self.assertEqual(result["status"], "PREPARED_READY")
        self.assertFalse(result["t0_started"])
        self.assertEqual(result["business_effects"], 0)
        self.assertEqual(result["classic_hosted_ui"], "READY")

    def test_active_or_cacheable_config_is_rejected_before_t0(self) -> None:
        source, release, values = self._responses()
        config_url = "https://example.cloudfront.net/runtime-config.json"
        config = json.loads(values[config_url].body)
        config["expiresAt"] = "2026-09-03T18:00:00Z"
        values[config_url] = readiness_probe.HttpResult(
            200, {"cache-control": "public,max-age=300"}, json.dumps(config).encode()
        )
        with self.assertRaises(readiness_probe.ReadinessError):
            readiness_probe.evaluate(
                web_url="https://example.cloudfront.net",
                expected_region="us-east-1",
                expected_source_revision=source,
                expected_release_sha256=release,
                fetcher=values.__getitem__,
            )

    def test_authenticated_route_must_deny_anonymous_access(self) -> None:
        source, release, values = self._responses()
        values["https://api123.execute-api.us-east-1.amazonaws.com/me"] = (
            readiness_probe.HttpResult(200, {}, b"{}")
        )
        with self.assertRaises(readiness_probe.ReadinessError):
            readiness_probe.evaluate(
                web_url="https://example.cloudfront.net",
                expected_region="us-east-1",
                expected_source_revision=source,
                expected_release_sha256=release,
                fetcher=values.__getitem__,
            )

    def test_classic_hosted_ui_must_be_publicly_ready(self) -> None:
        source, release, values = self._responses()
        login_url = next(url for url in values if "/oauth2/authorize?" in url)
        values[login_url] = readiness_probe.HttpResult(
            400, {"content-type": "text/html"}, b"<html>branding missing</html>"
        )
        with self.assertRaises(readiness_probe.ReadinessError):
            readiness_probe.evaluate(
                web_url="https://example.cloudfront.net",
                expected_region="us-east-1",
                expected_source_revision=source,
                expected_release_sha256=release,
                fetcher=values.__getitem__,
            )


if __name__ == "__main__":
    unittest.main()
