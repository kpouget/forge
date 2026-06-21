"""
Metrics collector hook for Locust — warmup period exclusion.

Registers event listeners to reset stats after the warmup period so
benchmark metrics only include steady-state performance.
"""

import os
import time

import gevent
from locust import events

_start_time = None
_warmup_seconds = 0


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global _start_time, _warmup_seconds
    _start_time = time.time()
    _warmup_seconds = int(os.environ.get("WARMUP_SECONDS", "0"))
    if _warmup_seconds > 0:

        def reset_after_warmup():
            print(f"[metrics] Warmup: {_warmup_seconds}s — stats will be reset after")
            gevent.sleep(_warmup_seconds)
            environment.runner.stats.reset_all()
            print("[metrics] Warmup complete: stats reset")

        gevent.spawn(reset_after_warmup)
