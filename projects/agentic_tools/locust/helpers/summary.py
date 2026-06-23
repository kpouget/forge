"""
Save Locust RunMetrics as separate metrics.json and parameters.json files.

These files follow a flat key-value JSON format that caliper's multi-run
export reads to log MLflow metrics and parameters automatically.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from projects.agentic_tools.locust.helpers.parse_results import RunMetrics

logger = logging.getLogger(__name__)


def save_metrics(metrics: RunMetrics, output_dir: Path) -> Path:
    """Write aggregated Locust metrics as flat JSON for caliper to pick up."""
    data = {
        "total_requests": metrics.total_requests,
        "total_failures": metrics.total_failures,
        "failure_rate": round(metrics.failure_rate, 6),
        "avg_response_time_ms": round(metrics.avg_response_time_ms, 3),
        "p50_ms": round(metrics.p50_ms, 3),
        "p90_ms": round(metrics.p90_ms, 3),
        "p95_ms": round(metrics.p95_ms, 3),
        "p99_ms": round(metrics.p99_ms, 3),
        "max_ms": round(metrics.max_ms, 3),
        "requests_per_second": round(metrics.requests_per_second, 3),
    }
    path = output_dir / "metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    logger.info("Metrics written to %s", path)
    return path


def save_parameters(output_dir: Path, **params: Any) -> Path:
    """Write test parameters as flat JSON for caliper to pick up."""
    clean = {str(k): ("" if v is None else v) for k, v in params.items()}
    path = output_dir / "parameters.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, sort_keys=True)
        f.write("\n")
    logger.info("Parameters written to %s", path)
    return path
