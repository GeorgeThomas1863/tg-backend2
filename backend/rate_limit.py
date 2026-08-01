"""Small in-memory rate limiter for failed authentication attempts."""

import math
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable


@dataclass
class AttemptRecord:
    count: int
    window_start: float


class AuthRateLimiter:
    """Track failed login attempts per client IP for one backend process."""

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._attempts: dict[str, AttemptRecord] = {}
        self._lock = Lock()

    def retry_after(self, client_ip: str) -> int | None:
        """Return lockout seconds for a limited IP, otherwise ``None``."""
        now = self._clock()
        with self._lock:
            self._prune_expired(now)
            record = self._attempts.get(client_ip)
            if record is None or record.count < self.max_attempts:
                return None
            remaining = self.window_seconds - (now - record.window_start)
            return max(1, math.ceil(remaining))

    def record_failure(self, client_ip: str) -> None:
        now = self._clock()
        with self._lock:
            self._prune_expired(now)
            record = self._attempts.get(client_ip)
            if record is None:
                self._attempts[client_ip] = AttemptRecord(1, now)
            else:
                record.count += 1

    def clear(self, client_ip: str) -> None:
        with self._lock:
            self._attempts.pop(client_ip, None)

    def _prune_expired(self, now: float) -> None:
        expired = [
            client_ip
            for client_ip, record in self._attempts.items()
            if now - record.window_start >= self.window_seconds
        ]
        for client_ip in expired:
            del self._attempts[client_ip]
