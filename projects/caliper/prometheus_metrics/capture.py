"""Prometheus metrics capture for OpenShift clusters.

Authenticates to the Thanos Querier via a ServiceAccount token, executes
PromQL range queries for a specified time window, and persists raw results
as JSON to the artifact tree.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from projects.caliper.prometheus_metrics.queries import load_queries

logger = logging.getLogger(__name__)


def _get_thanos_url() -> str:
    """Resolve the Thanos Querier in-cluster URL from the OpenShift route."""
    result = subprocess.run(
        [
            "oc",
            "get",
            "route",
            "thanos-querier",
            "-n",
            "openshift-monitoring",
            "-o",
            "jsonpath={.spec.host}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Failed to get thanos-querier route: {result.stderr.strip()}")
    return f"https://{result.stdout.strip()}"


def _get_prometheus_token() -> str:
    """Create a short-lived SA token for querying Prometheus."""
    result = subprocess.run(
        [
            "oc",
            "create",
            "token",
            "prometheus-k8s",
            "-n",
            "openshift-monitoring",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Failed to create prometheus token: {result.stderr.strip()}")
    return result.stdout.strip()


def _query_range(
    *,
    url: str,
    token: str,
    query: str,
    start: datetime,
    end: datetime,
    step_seconds: int,
) -> dict[str, Any]:
    """Execute a Prometheus range query via curl (avoids Python TLS issues with OCP)."""
    result = subprocess.run(
        [
            "curl",
            "-sk",
            "--max-time",
            "30",
            "-H",
            f"Authorization: Bearer {token}",
            f"{url}/api/v1/query_range",
            "--data-urlencode",
            f"query={query}",
            "--data-urlencode",
            f"start={start.timestamp():.3f}",
            "--data-urlencode",
            f"end={end.timestamp():.3f}",
            "--data-urlencode",
            f"step={step_seconds}s",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        logger.warning("Prometheus query failed: %s", result.stderr.strip())
        return {"status": "error", "error": result.stderr.strip()}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON from Prometheus: %s", e)
        return {"status": "error", "error": str(e)}


def capture_metrics(
    *,
    namespaces: list[str],
    start_time: datetime,
    end_time: datetime,
    step_seconds: int = 15,
    query_keys: list[str] | None = None,
    artifact_dir: Path | None = None,
    job_name: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """
    Query Prometheus for metrics and save raw results as JSON.

    Args:
        namespaces: Kubernetes namespaces to scope pod-level queries.
        start_time: Start of the query window (UTC).
        end_time: End of the query window (UTC).
        step_seconds: Query resolution step.
        query_keys: Subset of query keys from queries.yaml to execute.
            None or empty list means execute all available queries.
        artifact_dir: Root artifact directory for this run (used with job_name).
        job_name: Test job name (used for subdirectory under artifact_dir).
        output_dir: Explicit output directory. If set, overrides artifact_dir/job_name.

    Returns:
        Path to the metrics output directory.
    """
    if output_dir is not None:
        metrics_dir = output_dir
    elif artifact_dir is not None and job_name is not None:
        metrics_dir = artifact_dir / "artifacts" / "results" / job_name / "metrics" / "raw"
    else:
        raise ValueError("Either output_dir or both artifact_dir and job_name must be provided")
    metrics_dir.mkdir(parents=True, exist_ok=True)

    query_specs = load_queries(namespaces=namespaces, keys=query_keys or None)

    if not query_specs:
        if query_keys:
            raise ValueError(
                f"Explicit query keys resolved to zero queries: {query_keys}. "
                "Check that these keys exist in queries.yaml."
            )
        logger.warning("No query specs available")
        return metrics_dir

    logger.info(
        "Capturing %d metric queries (%s -> %s, step=%ds)",
        len(query_specs),
        start_time.strftime("%H:%M:%S"),
        end_time.strftime("%H:%M:%S"),
        step_seconds,
    )

    try:
        url = _get_thanos_url()
        token = _get_prometheus_token()
    except RuntimeError as e:
        logger.warning("Cannot connect to Prometheus, skipping metrics capture: %s", e)
        return metrics_dir

    for spec in query_specs:
        logger.info("  querying: %s (%s)", spec.key, spec.description)
        response = _query_range(
            url=url,
            token=token,
            query=spec.promql,
            start=start_time,
            end=end_time,
            step_seconds=step_seconds,
        )

        output = {
            "query_key": spec.key,
            "category": spec.category,
            "description": spec.description,
            "unit": spec.unit,
            "promql": spec.promql,
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "step_seconds": step_seconds,
            "response": response,
        }

        output_path = metrics_dir / f"{spec.key}.json"
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)
            f.write("\n")

    logger.info("Metrics saved to %s (%d files)", metrics_dir, len(query_specs))
    return metrics_dir
