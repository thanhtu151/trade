"""
Thread-safe vnstock source manager.
Rotates VCI → MSN on failure, resets to VCI every 10 minutes.
"""
import logging
import threading
import time

log = logging.getLogger("source_manager")

SOURCES = ["VCI", "MSN"]
_VCI_RESET_INTERVAL = 600  # 10 minutes


class _SourceManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._idx = 0           # current index into SOURCES
        self._status = "ok"     # "ok" | "fallback" | "all_failed"
        self._last_reset = time.monotonic()  # start timer from now, not epoch
        self._failures = [0] * len(SOURCES)

    def get_source(self) -> str:
        with self._lock:
            self._check_reset()
            return SOURCES[self._idx]

    def get_indicator(self) -> tuple:
        """Returns (source_name, status_label, css_color) for UI badges."""
        with self._lock:
            src = SOURCES[self._idx]
            if self._status == "ok":
                return src, "ok", "#3fb950"
            elif self._status == "all_failed":
                return src, "cache", "#f85149"
            else:
                return src, "fallback", "#d29922"

    def report_success(self, source: str):
        with self._lock:
            try:
                idx = SOURCES.index(source.upper())
                self._failures[idx] = 0
                if idx <= self._idx:
                    self._idx = idx
                    self._status = "ok" if idx == 0 else "fallback"
            except ValueError:
                pass

    def report_failure(self, source: str):
        with self._lock:
            try:
                idx = SOURCES.index(source.upper())
                self._failures[idx] += 1
                log.warning("source_manager: %s failed (%dx)", source, self._failures[idx])
                if idx == self._idx:
                    next_idx = idx + 1
                    if next_idx < len(SOURCES):
                        self._idx = next_idx
                        self._status = "fallback"
                        self._last_reset = time.monotonic()  # reset timer on switch
                        log.warning("source_manager: switching to %s", SOURCES[self._idx])
                    else:
                        self._status = "all_failed"
                        log.error("source_manager: all sources exhausted, will use stale cache")
            except ValueError:
                pass

    def _check_reset(self):
        """Try resetting to VCI after 10 min. Must be called under lock."""
        if self._idx != 0:
            now = time.monotonic()
            if now - self._last_reset >= _VCI_RESET_INTERVAL:
                log.info("source_manager: 10-min timer — resetting to VCI")
                self._last_reset = now
                self._idx = 0
                self._failures[0] = 0
                self._status = "ok"


_mgr = _SourceManager()


def get_source() -> str:
    return _mgr.get_source()


def get_indicator() -> tuple:
    return _mgr.get_indicator()


def report_success(source: str):
    _mgr.report_success(source)


def report_failure(source: str):
    _mgr.report_failure(source)
