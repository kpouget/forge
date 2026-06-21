"""
Shared Locust results → MLflow export.

Reads summary.json (produced by any Forge project's test phase), injects
parameters and metrics into the caliper MLflow config, then triggers the
caliper orchestration export. This module is project-agnostic: it only
requires the standard summary.json format with "parameters" and "metrics"
dicts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from projects.core.library import config, env

logger = logging.getLogger(__name__)


def generate_summary(
    *,
    metrics: Any,
    extra_params: dict[str, Any] | None = None,
    **params: Any,
) -> dict[str, Any]:
    """
    Generate a standard summary dict from parsed Locust RunMetrics.

    Args:
        metrics: RunMetrics dataclass from parse_results
        extra_params: Additional parameters to include (optional)
        **params: Core parameters (preset, target, users, etc.)
    """
    all_params = dict(params)
    if extra_params:
        all_params.update(extra_params)

    return {
        "parameters": all_params,
        "metrics": {
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
        },
        "per_request": metrics.per_request_metrics,
    }


def save_summary(*, summary: dict[str, Any], artifact_dir: Path, job_name: str) -> Path:
    """Write the summary JSON to the standard artifact location."""
    output_path = artifact_dir / "artifacts" / "results" / job_name / "summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")
    logger.info("Summary written to %s", output_path)
    return output_path


def export_to_mlflow(*, run_name: str, artifact_directory: Path | None = None) -> None:
    """
    Export artifacts to MLflow if configured, injecting Locust summary
    metrics and parameters so they appear as first-class MLflow metrics.
    """
    import os

    mlflow_enabled = config.project.get_config(
        "caliper.export.backend.mlflow.enabled", False, print=False, warn=False
    )
    if not mlflow_enabled:
        logger.info("MLflow export not enabled, skipping")
        return

    try:
        from projects.core.library.export import run_caliper_orchestration_export

        workspace = config.project.get_config(
            "caliper.export.backend.mlflow.config.workspace", None, print=False, warn=False
        )
        if workspace:
            os.environ["MLFLOW_WORKSPACE"] = workspace

        config.project.set_config(
            "caliper.export.backend.mlflow.config.run_name", run_name, print=False
        )

        effective_dir = artifact_directory or Path(str(env.ARTIFACT_DIR))

        _inject_summary_into_mlflow_config(effective_dir)

        status = run_caliper_orchestration_export(artifact_directory=effective_dir)
        logger.info("MLflow export completed: %s", status)
    except Exception as e:
        logger.warning("MLflow export failed (non-fatal): %s", e)


def _inject_summary_into_mlflow_config(artifact_directory: Path) -> None:
    """
    Find the latest Locust summary.json in the artifact tree and inject its
    parameters and metrics into the caliper MLflow config so they are logged
    as MLflow params/metrics (not just as file artifacts).
    """
    results_dir = artifact_directory / "artifacts" / "results"
    if not results_dir.is_dir():
        return

    summary_files = sorted(results_dir.glob("*/summary.json"), key=lambda p: p.stat().st_mtime)
    if not summary_files:
        return

    latest_summary = summary_files[-1]
    try:
        with open(latest_summary) as f:
            summary = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read summary for MLflow metadata: %s", e)
        return

    params = summary.get("parameters", {})
    metrics = summary.get("metrics", {})

    mlflow_params = {str(k): ("" if v is None else str(v)) for k, v in params.items()}
    mlflow_metrics = {
        str(k): v
        for k, v in metrics.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }

    if mlflow_params:
        config.project.set_config(
            "caliper.export.backend.mlflow.config.parameters", mlflow_params, print=False
        )
    if mlflow_metrics:
        config.project.set_config(
            "caliper.export.backend.mlflow.config.metrics", mlflow_metrics, print=False
        )

    logger.info(
        "Injected Locust summary into MLflow config: %d params, %d metrics (from %s)",
        len(mlflow_params),
        len(mlflow_metrics),
        latest_summary.name,
    )
