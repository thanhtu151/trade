"""Bounded transient retry and in-process circuit breakers."""

from __future__ import annotations

import random
import json
import os
import tempfile
import threading
import time
from pathlib import Path


_PERSIST_LOCK = threading.Lock()


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold=3, recovery_seconds=300, clock=None, state_path=None, namespace="default"):
        self.failure_threshold = int(failure_threshold)
        self.recovery_seconds = float(recovery_seconds)
        # Wall-clock timestamps remain meaningful across separate Actions runs.
        self.clock = clock or time.time
        self._states = {}
        self._lock = threading.Lock()
        self.state_path = Path(state_path) if state_path else None
        self.namespace = str(namespace)
        self._load()

    def _load(self):
        if not self.state_path:
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            states = payload.get(self.namespace, {})
            if isinstance(states, dict):
                self._states = states
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._states = {}

    def _persist(self):
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with _PERSIST_LOCK:
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    payload = {}
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                payload = {}
            payload[self.namespace] = self._states
            fd, temp_name = tempfile.mkstemp(prefix=self.state_path.name + ".", dir=str(self.state_path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.state_path)
            finally:
                if os.path.exists(temp_name):
                    os.remove(temp_name)

    def allow(self, key):
        with self._lock:
            state = self._states.get(str(key), {"failures": 0, "opened_at": None})
            opened_at = state.get("opened_at")
            if opened_at is None:
                return True
            if self.clock() - opened_at >= self.recovery_seconds:
                state["failures"] = 0
                state["opened_at"] = None
                self._states[str(key)] = state
                self._persist()
                return True
            return False

    def success(self, key):
        with self._lock:
            self._states[str(key)] = {"failures": 0, "opened_at": None}
            self._persist()

    def failure(self, key):
        with self._lock:
            state = self._states.setdefault(str(key), {"failures": 0, "opened_at": None})
            state["failures"] += 1
            if state["failures"] >= self.failure_threshold:
                state["opened_at"] = self.clock()
            self._persist()


def exponential_backoff(attempt, base_delay=1.0, max_delay=30.0, jitter_ratio=0.25, random_fn=None):
    random_fn = random_fn or random.random
    delay = min(float(max_delay), float(base_delay) * (2 ** max(0, int(attempt))))
    return delay + delay * float(jitter_ratio) * random_fn()


def retry_transient(
    operation,
    *,
    attempts=3,
    base_delay=0.5,
    max_delay=8.0,
    jitter_ratio=0.25,
    is_transient=None,
    sleeper=None,
    random_fn=None,
):
    """Retry a transient operation with bounded exponential backoff and jitter."""
    attempts = max(1, int(attempts))
    sleeper = sleeper or time.sleep
    random_fn = random_fn or random.random
    is_transient = is_transient or (lambda exc: isinstance(exc, (TimeoutError, ConnectionError, OSError)))
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if attempt + 1 >= attempts or not is_transient(exc):
                raise
            delay = exponential_backoff(attempt, base_delay, max_delay, jitter_ratio, random_fn)
            sleeper(delay)


_STATE_PATH = Path(__file__).resolve().parent / "circuit_breaker_state.json"
market_data_circuit = CircuitBreaker(failure_threshold=3, recovery_seconds=300, state_path=_STATE_PATH, namespace="market_data")
llm_circuit = CircuitBreaker(failure_threshold=3, recovery_seconds=300, state_path=_STATE_PATH, namespace="llm")


def is_transient_network_error(exc):
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status in {408, 409, 425, 429} or (isinstance(status, int) and status >= 500):
        return True
    text = str(exc).lower()
    return any(token in text for token in ("timeout", "temporar", "connection reset", "rate limit", "overloaded"))
