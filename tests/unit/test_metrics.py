import re

import pytest

pytestmark = pytest.mark.asyncio


async def test_metrics_endpoint_exposes_prometheus_format(client):
    await client.get("/health")
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "agriflow_http_requests_total" in resp.text
    assert 'path_template="/health"' in resp.text


async def test_metrics_endpoint_never_leaks_secrets(client):
    """Sanity check: metrics text must never contain request bodies,
    auth headers, or anything from the redaction list in
    app.core.logging — it only records method/path-template/status/
    latency (Master Plan §49).

    Route templates are exempt from the keyword scan: a path such as
    /managers/{manager_id}/reset-password legitimately contains the word
    "password" while carrying no secret. What must never appear is a
    secret VALUE, so the scan runs over everything except the
    path_template label."""
    from app.core.config import settings

    resp = await client.get("/metrics")
    without_templates = re.sub(r'path_template="[^"]*"', 'path_template=""', resp.text).lower()

    for forbidden in ("password", "jwt_secret", "bearer ", "authorization", "otp"):
        assert forbidden not in without_templates

    assert settings.JWT_SECRET_KEY.lower() not in resp.text.lower()
