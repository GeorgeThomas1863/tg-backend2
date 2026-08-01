"""Unit tests for the in-memory authentication rate limiter."""

from rate_limit import AuthRateLimiter


def test_ip_is_limited_after_maximum_failures():
    limiter = AuthRateLimiter(max_attempts=2, window_seconds=60)

    limiter.record_failure("192.0.2.1")
    assert limiter.retry_after("192.0.2.1") is None

    limiter.record_failure("192.0.2.1")
    assert limiter.retry_after("192.0.2.1") == 60


def test_different_ips_have_independent_counts():
    limiter = AuthRateLimiter(max_attempts=1, window_seconds=60)

    limiter.record_failure("192.0.2.1")

    assert limiter.retry_after("192.0.2.1") == 60
    assert limiter.retry_after("192.0.2.2") is None


def test_clear_removes_prior_failures():
    limiter = AuthRateLimiter(max_attempts=1, window_seconds=60)
    limiter.record_failure("192.0.2.1")

    limiter.clear("192.0.2.1")

    assert limiter.retry_after("192.0.2.1") is None


def test_attempts_expire_after_window():
    now = [100.0]
    limiter = AuthRateLimiter(
        max_attempts=1,
        window_seconds=60,
        clock=lambda: now[0],
    )
    limiter.record_failure("192.0.2.1")

    now[0] = 160.0

    assert limiter.retry_after("192.0.2.1") is None
