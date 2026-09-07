#!/usr/bin/env python3
"""Verify the publicly served PREPARED pilot without business side effects."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import publish_frontend


class ReadinessError(RuntimeError):
    """The staged public deployment is not safely PREPARED."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


def fetch(url: str, *, timeout_seconds: float = 10) -> HttpResult:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json,text/html", "User-Agent": "approvals-readiness/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return HttpResult(
                int(response.status),
                {key.lower(): value for key, value in response.headers.items()},
                response.read(1_048_577),
            )
    except urllib.error.HTTPError as exc:
        return HttpResult(
            int(exc.code),
            {key.lower(): value for key, value in exc.headers.items()},
            exc.read(1_048_577),
        )
    except (OSError, urllib.error.URLError) as exc:
        raise ReadinessError("Public readiness endpoint is unavailable") from exc


def _json_body(result: HttpResult, label: str) -> dict[str, Any]:
    if len(result.body) > 1_048_576:
        raise ReadinessError(f"{label} response exceeds the bounded size")
    try:
        value = json.loads(result.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"{label} did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"{label} JSON root is invalid")
    return value


def authorization_probe_url(config: Mapping[str, Any]) -> str:
    """Build a side-effect-free PKCE authorize request for the configured login UI."""

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config["cognitoClientId"],
            "redirect_uri": config["redirectUri"],
            "scope": "openid",
            "code_challenge": "A" * 43,
            "code_challenge_method": "S256",
            "state": "approvals-prepared-readiness",
        }
    )
    return str(config["cognitoDomain"]).rstrip("/") + "/oauth2/authorize?" + query


def evaluate(
    *,
    web_url: str,
    expected_region: str,
    expected_source_revision: str,
    expected_release_sha256: str,
    fetcher: Callable[[str], HttpResult],
) -> dict[str, Any]:
    if re.fullmatch(r"https://[a-z0-9-]+\.cloudfront\.net", web_url) is None:
        raise ReadinessError("Web URL is not an exact HTTPS CloudFront origin")
    root = fetcher(web_url + "/")
    if root.status != 200 or b'id="root"' not in root.body:
        raise ReadinessError("CloudFront does not serve the reviewed PWA shell")

    config_result = fetcher(web_url + "/runtime-config.json")
    if config_result.status != 200 or "no-store" not in config_result.headers.get(
        "cache-control", ""
    ).lower():
        raise ReadinessError("PREPARED runtime config is missing or cacheable")
    config = _json_body(config_result, "runtime-config.json")
    publish_frontend.validate_prepared_runtime_config(config, expected_region)
    binding = config["deploymentBinding"]
    if binding != {
        "sourceRevision": expected_source_revision,
        "frontendReleaseSha256": expected_release_sha256,
    }:
        raise ReadinessError("Public deployment binding does not match the reviewed candidate")

    login = fetcher(authorization_probe_url(config))
    if (
        login.status != 200
        or "text/html" not in login.headers.get("content-type", "").lower()
        or b"<html" not in login.body.lower()
    ):
        raise ReadinessError("Cognito classic Hosted UI is not ready for PKCE login")

    api_base = str(config["apiBaseUrl"]).rstrip("/")
    health = fetcher(api_base + "/health")
    if health.status != 200 or _json_body(health, "GET /health") != {
        "status": "PREPARED",
        "expiresAt": None,
        "syntheticData": True,
        "pii": False,
    }:
        raise ReadinessError("Durable pilot state is not exactly PREPARED")
    if fetcher(api_base + "/me").status != 401:
        raise ReadinessError("Unauthenticated business route is not denied")

    return {
        "status": "PREPARED_READY",
        "source_revision": expected_source_revision,
        "frontend_release_sha256": expected_release_sha256,
        "web_origin_hash": hashlib.sha256(web_url.encode()).hexdigest(),
        "business_effects": 0,
        "t0_started": False,
        "classic_hosted_ui": "READY",
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--web-url", required=True)
    value.add_argument("--expected-region", required=True)
    value.add_argument("--expected-source-revision", required=True)
    value.add_argument("--expected-release-sha256", required=True)
    value.add_argument("--attempts", type=int, default=12)
    value.add_argument("--interval-seconds", type=float, default=5)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.attempts <= 60 or not 0 <= args.interval_seconds <= 30:
        print("prepared readiness: FAIL: retry bounds are invalid", file=sys.stderr)
        return 1
    last_error: ReadinessError | None = None
    for attempt in range(args.attempts):
        try:
            result = evaluate(
                web_url=args.web_url.rstrip("/"),
                expected_region=args.expected_region,
                expected_source_revision=args.expected_source_revision,
                expected_release_sha256=args.expected_release_sha256,
                fetcher=fetch,
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        except (ReadinessError, publish_frontend.FrontendPublishError) as exc:
            last_error = ReadinessError(str(exc))
            if attempt + 1 < args.attempts:
                time.sleep(args.interval_seconds)
    print(f"prepared readiness: FAIL: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
