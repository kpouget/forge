"""
Parse Locust CSV stats into structured metrics.

This is a generic Locust results parser that works with any Locust CSV output.
Project-specific summary generation (KPIs, labels) should be done by the
consuming project.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    s = str(value).strip()
    if not s or s == "N/A":
        return default
    return float(s)


def _safe_int(value, default: int = 0) -> int:
    if value is None:
        return default
    s = str(value).strip()
    if not s or s == "N/A":
        return default
    return int(float(s))


@dataclass
class RunMetrics:
    """Aggregated metrics from a single Locust run."""

    total_requests: int = 0
    total_failures: int = 0
    failure_rate: float = 0.0
    avg_response_time_ms: float = 0.0
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    max_ms: float = 0.0
    requests_per_second: float = 0.0
    per_request_metrics: dict[str, dict[str, float]] = field(default_factory=dict)


def parse_stats_csv(stats_csv: str) -> RunMetrics:
    """Parse Locust stats CSV into RunMetrics."""
    if not stats_csv.strip():
        logger.warning("Empty stats CSV provided")
        return RunMetrics()

    reader = csv.DictReader(io.StringIO(stats_csv))
    metrics = RunMetrics()
    per_request: dict[str, dict[str, float]] = {}

    for row in reader:
        name = row.get("Name", "")
        request_type = row.get("Type", "")

        if name == "Aggregated":
            metrics.total_requests = _safe_int(row.get("Request Count", 0))
            metrics.total_failures = _safe_int(row.get("Failure Count", 0))
            metrics.avg_response_time_ms = _safe_float(row.get("Average Response Time", 0))
            metrics.p50_ms = _safe_float(row.get("50%", 0))
            metrics.p90_ms = _safe_float(row.get("90%", 0))
            metrics.p95_ms = _safe_float(row.get("95%", 0))
            metrics.p99_ms = _safe_float(row.get("99%", 0))
            metrics.max_ms = _safe_float(row.get("Max Response Time", 0))
            metrics.requests_per_second = _safe_float(row.get("Requests/s", 0))

            if metrics.total_requests > 0:
                metrics.failure_rate = metrics.total_failures / metrics.total_requests
        else:
            per_request[f"{request_type}:{name}"] = {
                "count": _safe_int(row.get("Request Count", 0)),
                "failures": _safe_int(row.get("Failure Count", 0)),
                "avg_ms": _safe_float(row.get("Average Response Time", 0)),
                "p50_ms": _safe_float(row.get("50%", 0)),
                "p95_ms": _safe_float(row.get("95%", 0)),
                "p99_ms": _safe_float(row.get("99%", 0)),
                "rps": _safe_float(row.get("Requests/s", 0)),
            }

    metrics.per_request_metrics = per_request
    return metrics
