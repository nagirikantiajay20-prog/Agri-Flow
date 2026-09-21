"""
In-process Redis for the cache / OTP / live-update tests.

Uses fakeredis rather than requiring a Redis service, so these tests are
deterministic on a laptop and in CI alike. The concurrency and IDOR
suites still run against real Postgres — see tests/conftest.py for why.

Modules that did `from app.core.redis import get_redis` hold their own
reference to it, so patching only `app.core.redis` would leave them
talking to a real server. The patch is therefore applied to every
already-imported `app.*` module exposing a `get_redis`, discovered at
fixture time — an explicit list silently rots the moment a new module
imports it, which is exactly how the SSE endpoint first escaped it.
"""
import sys

import fakeredis.aioredis
import pytest

from app.core import redis as redis_module


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)

    monkeypatch.setattr(redis_module, "_client", client, raising=False)
    monkeypatch.setattr(redis_module, "get_redis", lambda: client)
    redis_module.mark_available()

    for name, module in list(sys.modules.items()):
        if not name.startswith("app.") or module is redis_module or module is None:
            continue
        if getattr(module, "get_redis", None) is not None:
            monkeypatch.setattr(module, "get_redis", lambda: client, raising=False)

    yield client
