import pytest

from architeq_api import security
from architeq_api.config import Settings


@pytest.fixture
def strict_settings(monkeypatch):
    """SSRF checks on (conftest sets ARCHITEQ_ALLOW_PRIVATE_WEBHOOKS=true)."""
    monkeypatch.setattr(security, "get_settings", lambda: Settings(allow_private_webhooks=False))


class TestAssertUrlSafe:
    def test_rejects_non_http_scheme(self, strict_settings):
        with pytest.raises(security.UnsafeUrlError):
            security.assert_url_safe("ftp://example.com/x")
        with pytest.raises(security.UnsafeUrlError):
            security.assert_url_safe("file:///etc/passwd")

    def test_rejects_loopback(self, strict_settings):
        with pytest.raises(security.UnsafeUrlError):
            security.assert_url_safe("http://127.0.0.1:8080/internal/steal")
        with pytest.raises(security.UnsafeUrlError):
            security.assert_url_safe("http://localhost/x")

    def test_rejects_private_ranges(self, strict_settings):
        for host in ("10.0.0.5", "192.168.1.1", "172.16.3.4"):
            with pytest.raises(security.UnsafeUrlError):
                security.assert_url_safe(f"https://{host}/hook")

    def test_rejects_gcp_metadata(self, strict_settings):
        with pytest.raises(security.UnsafeUrlError):
            security.assert_url_safe("http://169.254.169.254/computeMetadata/v1/")

    def test_rejects_missing_host(self, strict_settings):
        with pytest.raises(security.UnsafeUrlError):
            security.assert_url_safe("https:///path-only")

    def test_allows_public_ip(self, strict_settings):
        security.assert_url_safe("https://8.8.8.8/webhook")

    def test_dev_escape_hatch_allows_everything(self, monkeypatch):
        monkeypatch.setattr(security, "get_settings", lambda: Settings(allow_private_webhooks=True))
        security.assert_url_safe("http://127.0.0.1/x")


class TestRateLimiter:
    def test_allows_under_limit(self):
        limiter = security.RateLimiter(limit_per_minute=3)
        assert all(limiter.check("k") for _ in range(3))

    def test_blocks_over_limit(self):
        limiter = security.RateLimiter(limit_per_minute=2)
        limiter.check("k")
        limiter.check("k")
        assert limiter.check("k") is False

    def test_keys_are_independent(self):
        limiter = security.RateLimiter(limit_per_minute=1)
        assert limiter.check("a")
        assert limiter.check("b")
        assert limiter.check("a") is False

    def test_zero_limit_disables(self):
        limiter = security.RateLimiter(limit_per_minute=0)
        assert all(limiter.check("k") for _ in range(100))

    def test_window_slides(self, monkeypatch):
        import time as time_mod

        t = [1000.0]
        monkeypatch.setattr(time_mod, "monotonic", lambda: t[0])
        limiter = security.RateLimiter(limit_per_minute=1)
        assert limiter.check("k")
        assert limiter.check("k") is False
        t[0] += 61
        assert limiter.check("k")


async def test_security_headers_present(client):
    resp = await client.get("/healthz")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


async def test_internal_token_rejects_wrong_and_missing(client):
    resp = await client.post(
        "/internal/inbound/resolve",
        headers={"X-Internal-Token": "wrong"},
        json={"from_number": "+1", "to_number": "+1"},
    )
    assert resp.status_code == 401
    resp = await client.post(
        "/internal/inbound/resolve", json={"from_number": "+1", "to_number": "+1"}
    )
    assert resp.status_code == 401
