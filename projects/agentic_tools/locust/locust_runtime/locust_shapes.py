"""
Load shape definitions for Llama Stack benchmarks.

All shapes in a single file for flat ConfigMap compatibility.
Selected via LOAD_SHAPE environment variable.

Shapes:
    steady    - constant user count for entire duration
    spike     - sudden burst then return to baseline
    realistic - gradual ramp up, plateau, gradual ramp down
    poisson   - random variation around target user count
    custom    - stages defined via CUSTOM_STAGES env var JSON
"""

import json
import os
import random

from locust import LoadTestShape


def _parse_env(default_duration="60"):
    """Parse and clamp common load-shape env vars to safe positive integers."""
    users = max(1, int(os.environ.get("USERS", "10")))
    spawn_rate = max(1, int(os.environ.get("SPAWN_RATE", "1")))
    duration = max(
        1, int(os.environ.get("RUN_TIME_SECONDS", os.environ.get("DURATION", default_duration)))
    )
    return users, spawn_rate, duration


class SteadyShape(LoadTestShape):
    """Constant user count for the entire duration."""

    def tick(self):
        users, spawn_rate, duration = _parse_env("60")

        if self.get_run_time() > duration:
            return None
        return (users, spawn_rate)


class SpikeShape(LoadTestShape):
    """Sudden burst at 30-50% of duration, then return to baseline."""

    def tick(self):
        users, spawn_rate, duration = _parse_env("120")
        run_time = self.get_run_time()

        if run_time > duration:
            return None

        spike_start = duration * 0.3
        spike_end = duration * 0.5

        if spike_start <= run_time <= spike_end:
            return (users * 5, spawn_rate * 10)
        return (users, spawn_rate)


class RealisticShape(LoadTestShape):
    """Gradual ramp up (20%), plateau (60%), gradual ramp down (20%)."""

    def tick(self):
        users, spawn_rate, duration = _parse_env("180")
        run_time = self.get_run_time()

        if run_time > duration:
            return None

        ramp_up_end = duration * 0.2
        plateau_end = duration * 0.8

        if run_time <= ramp_up_end:
            current_users = max(1, int(users * (run_time / ramp_up_end)))
        elif run_time <= plateau_end:
            current_users = users
        else:
            progress = (run_time - plateau_end) / (duration - plateau_end)
            current_users = max(1, int(users * (1 - progress)))

        return (current_users, spawn_rate)


class PoissonShape(LoadTestShape):
    """Random user count around the target (uniform ±50%)."""

    def tick(self):
        users, spawn_rate, duration = _parse_env("120")

        if self.get_run_time() > duration:
            return None

        current_users = random.randint(max(1, users // 2), max(1, users * 2))
        return (current_users, spawn_rate)


class CustomShape(LoadTestShape):
    """Stages defined via CUSTOM_STAGES env var (JSON array)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stages_json = os.environ.get("CUSTOM_STAGES", "[]")
        try:
            self.stages = json.loads(stages_json) if stages_json else []
        except (json.JSONDecodeError, TypeError):
            print("WARNING: Malformed CUSTOM_STAGES JSON, falling back to empty stages")
            self.stages = []

    def tick(self):
        run_time = self.get_run_time()
        if not self.stages:
            return None

        for stage in self.stages:
            if run_time <= stage.get("duration", 60):
                return (stage.get("users", 10), stage.get("spawn_rate", 1))
        return None


# Shape registry
_SHAPES = {
    "steady": SteadyShape,
    "spike": SpikeShape,
    "realistic": RealisticShape,
    "poisson": PoissonShape,
    "custom": CustomShape,
}


def get_shape_class():
    """Import and return the shape class based on LOAD_SHAPE env var.

    Locust discovers LoadTestShape subclasses in the module namespace,
    so we just need to ensure the selected class is non-abstract.
    """
    shape_name = os.environ.get("LOAD_SHAPE", "steady")
    cls = _SHAPES.get(shape_name)
    if cls is None:
        print(f"WARNING: Unknown shape '{shape_name}', using steady")
        cls = SteadyShape
    return cls
