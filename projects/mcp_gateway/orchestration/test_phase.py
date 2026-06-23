"""
Test phase for MCP Gateway performance tests.

Iterates over the experiment matrix (servers x concurrency x target)
and executes a load test for each combination. Each test iteration
produces a run directory under ``$ARTIFACT_DIR/runs/<job-name>/``
containing metrics.json, parameters.json, and raw Locust artifacts.
Caliper picks these up during the export-artifacts phase to create
per-iteration MLflow runs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from projects.agentic_tools.locust.helpers.parse_results import parse_stats_csv
from projects.agentic_tools.locust.helpers.summary import save_metrics, save_parameters
from projects.agentic_tools.locust.toolbox.run_distributed import main as run_locust
from projects.agentic_tools.locust.toolbox.run_distributed.main import LocustResults
from projects.agentic_tools.mcp.toolbox.deploy_mock_servers import main as deploy_mock_servers
from projects.caliper.prometheus_metrics.capture import capture_metrics
from projects.caliper.prometheus_metrics.config import MetricsCaptureConfig
from projects.core.dsl.utils import write_json
from projects.core.library import env
from projects.mcp_gateway.orchestration.runtime_config import cfg
from projects.mcp_gateway.toolbox.apply_infrastructure import main as apply_infra

logger = logging.getLogger(__name__)


def run() -> int:
    """Execute the experiment matrix: servers x concurrency x target."""
    namespace = cfg.get_namespace()
    preset = cfg.get_preset_name()
    mock_server = cfg.get_mock_server_key()
    mock_server_cfg = cfg.get_mock_server_config()
    version = cfg.get_deployed_version()

    servers = cfg.get_experiment_servers()
    concurrency = cfg.get_experiment_concurrency()
    targets = cfg.get_experiment_targets()
    tools_per_server = cfg.get_tools_per_server()
    scheduling = cfg.get_scheduling_config()
    warmup_seconds = cfg.get_warmup_seconds()
    metrics_cfg = MetricsCaptureConfig(**cfg.get_metrics_config())

    total = len(servers) * len(concurrency) * len(targets)
    logger.info(
        "Test matrix: %d servers x %d concurrency x %d targets = %d jobs",
        len(servers),
        len(concurrency),
        len(targets),
        total,
    )

    runs_dir = env.ARTIFACT_DIR / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    all_summaries: list[dict[str, Any]] = []

    for num_servers in servers:
        for users in concurrency:
            for target in targets:
                job_name = f"mcp-{preset}-s{num_servers}-u{users}-{target}"[:63]
                run_dir = runs_dir / job_name
                run_dir.mkdir(parents=True, exist_ok=True)

                logger.info(
                    "[%s] servers=%d users=%d target=%s",
                    job_name,
                    num_servers,
                    users,
                    target,
                )

                try:
                    _deploy_servers(
                        namespace=namespace,
                        num_servers=num_servers,
                        targets=[target],
                        mock_server_cfg=mock_server_cfg,
                        tools_per_server=tools_per_server,
                        scheduling=scheduling,
                    )

                    run_start_time = datetime.now(UTC)
                    results = _run_test(users=users, target=target, num_servers=num_servers)
                    test_end_time = datetime.now(UTC)
                    test_start_time = run_start_time + timedelta(seconds=warmup_seconds)

                    metrics = parse_stats_csv(results.stats_csv)

                    save_metrics(metrics, run_dir)
                    save_parameters(
                        run_dir,
                        preset=preset,
                        target=target,
                        users=users,
                        mock_server=mock_server,
                        mcp_gateway_version=version,
                        num_servers=num_servers,
                        tools_per_server=tools_per_server,
                    )

                    _save_locust_artifacts(results, run_dir)
                    _capture_pod_logs(namespace=namespace, run_dir=run_dir)

                    if metrics_cfg.enabled:
                        metrics_out = run_dir / "metrics" / "raw"
                        metrics_out.mkdir(parents=True, exist_ok=True)
                        capture_metrics(
                            namespaces=metrics_cfg.namespaces,
                            start_time=test_start_time,
                            end_time=test_end_time,
                            step_seconds=metrics_cfg.step_seconds,
                            query_keys=metrics_cfg.query_keys or None,
                            output_dir=metrics_out,
                        )

                    all_summaries.append(job_name)
                finally:
                    _cleanup_locust_job(namespace=namespace, job_name=job_name)
                    _cleanup_servers(
                        namespace=namespace,
                        num_servers=num_servers,
                        mock_server=mock_server,
                    )

    write_json(
        env.ARTIFACT_DIR / "runs" / "test_summary.json",
        {
            "preset": preset,
            "version": version,
            "servers": servers,
            "concurrency": concurrency,
            "targets": targets,
            "tools_per_server": tools_per_server,
            "run_names": all_summaries,
        },
    )

    return 0


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def _save_locust_artifacts(results: LocustResults, run_dir: Path) -> None:
    """Save raw Locust CSV/log output directly to the run directory."""
    if results.stats_csv:
        (run_dir / "stats.csv").write_text(results.stats_csv, encoding="utf-8")
    if results.stats_history_csv:
        (run_dir / "stats_history.csv").write_text(results.stats_history_csv, encoding="utf-8")
    if results.failures_csv:
        (run_dir / "failures.csv").write_text(results.failures_csv, encoding="utf-8")
    (run_dir / "master.log").write_text(results.raw_log, encoding="utf-8")
    logger.info("Locust artifacts saved to %s", run_dir)


# ---------------------------------------------------------------------------
# Deployment helpers
# ---------------------------------------------------------------------------


def _deploy_servers(
    *,
    namespace: str,
    num_servers: int,
    targets: list[str],
    mock_server_cfg: dict[str, Any],
    tools_per_server: int,
    scheduling: dict[str, Any] | None = None,
) -> None:
    """Deploy mock server(s) and gateway infrastructure."""
    image = mock_server_cfg.get("image", "quay.io/rh-ee-aharush/perf-mock-server:latest")
    sched = scheduling or {}

    deploy_mock_servers.deploy_servers(
        namespace=namespace,
        count=num_servers,
        image=image,
        tools_per_server=tools_per_server,
        labels={"forge.openshift.io/project": "mcp_gateway"},
        node_selector=sched.get("node_selector"),
        tolerations=sched.get("tolerations"),
    )

    if "gateway" in targets:
        api_group = cfg.get_api_group()
        apply_infra.run(
            namespace=namespace,
            count=num_servers,
            api_group=api_group,
        )


def _cleanup_servers(*, namespace: str, num_servers: int, mock_server: str) -> None:
    """Remove mock servers and infrastructure after a server-level iteration."""
    from projects.core.dsl.utils.k8s import oc as run_oc

    deploy_mock_servers.cleanup_servers(namespace=namespace)

    api_group = cfg.get_api_group()
    scale_out_label = "experiment=scale-out"
    run_oc(
        "delete",
        f"mcpserverregistrations.{api_group},httproute",
        "-n",
        namespace,
        "-l",
        scale_out_label,
        "--wait=false",
        "--ignore-not-found=true",
        check=False,
    )
    run_oc(
        "delete",
        "destinationrule",
        "-n",
        "istio-system",
        "-l",
        scale_out_label,
        "--wait=false",
        "--ignore-not-found=true",
        check=False,
    )


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------


def _run_test(*, users: int, target: str, num_servers: int) -> LocustResults:
    """Run a single Locust test (artifacts saved separately by the caller)."""
    locust_kwargs = cfg.build_locust_kwargs(
        users=users,
        target=target,
        num_servers=num_servers,
    )
    ctx = run_locust.run(**locust_kwargs, cleanup=False)
    return ctx.results


def _cleanup_locust_job(*, namespace: str, job_name: str) -> None:
    """Remove Locust master, workers, and headless service for a single test iteration."""
    from projects.core.dsl.utils.k8s import oc

    for resource in (
        f"job/{job_name}-master",
        f"job/{job_name}-workers",
        f"svc/{job_name}-master",
    ):
        oc("delete", resource, "-n", namespace, "--ignore-not-found=true", check=False)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _capture_pod_logs(*, namespace: str, run_dir: Path) -> None:
    """Capture logs from all pods in test namespace and gateway infrastructure namespaces."""
    from projects.core.dsl.utils.k8s import capture_pod_logs

    logs_dir = run_dir / "pod_logs"
    capture_pod_logs(namespace=namespace, output_dir=logs_dir)

    for gw_ns in ("mcp-system", "gateway-system"):
        gw_logs_dir = logs_dir / gw_ns
        capture_pod_logs(namespace=gw_ns, output_dir=gw_logs_dir)
