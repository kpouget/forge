"""
Test phase for MCP Gateway performance tests.

Iterates over the experiment matrix (servers × concurrency × target)
and executes a load test for each combination.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from projects.agentic_tools.locust.toolbox.export_to_mlflow.main import (
    export_to_mlflow,
    generate_summary,
    save_summary,
)
from projects.agentic_tools.locust.toolbox.parse_results.main import parse_stats_csv
from projects.agentic_tools.locust.toolbox.run_distributed import main as run_locust
from projects.agentic_tools.locust.toolbox.run_distributed.main import LocustResults
from projects.agentic_tools.locust.toolbox.run_distributed.main import (
    cleanup_job as cleanup_locust_job,
)
from projects.agentic_tools.mcp.toolbox.deploy_mock_servers import main as deploy_mock_servers
from projects.caliper.metrics.capture import capture_metrics
from projects.caliper.metrics.config import MetricsCaptureConfig
from projects.caliper.metrics.plot import generate_plots
from projects.core.dsl.utils import write_json
from projects.core.library import env
from projects.mcp_gateway.orchestration.runtime_config import cfg
from projects.mcp_gateway.toolbox.apply_infrastructure import main as apply_infra

logger = logging.getLogger(__name__)


def run() -> int:
    """Execute the experiment matrix: servers × concurrency × target."""
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
        "Test matrix: %d servers × %d concurrency × %d targets = %d jobs",
        len(servers),
        len(concurrency),
        len(targets),
        total,
    )

    all_summaries: list[dict[str, Any]] = []

    for num_servers in servers:
        _deploy_servers(
            namespace=namespace,
            num_servers=num_servers,
            targets=targets,
            mock_server_cfg=mock_server_cfg,
            tools_per_server=tools_per_server,
            scheduling=scheduling,
        )

        for users in concurrency:
            if num_servers == 1:
                deploy_mock_servers.restart_servers(namespace=namespace, count=1)

            for target in targets:
                job_name = f"mcp-{preset}-s{num_servers}-u{users}-{target}"[:63]
                logger.info(
                    "[%s] servers=%d users=%d target=%s", job_name, num_servers, users, target
                )

                run_start_time = datetime.now(UTC)
                results = _run_test(users=users, target=target, num_servers=num_servers)
                test_end_time = datetime.now(UTC)
                test_start_time = run_start_time + timedelta(seconds=warmup_seconds)

                metrics = parse_stats_csv(results.stats_csv)
                summary = generate_summary(
                    metrics=metrics,
                    preset=preset,
                    target=target,
                    users=users,
                    mock_server=mock_server,
                    mcp_gateway_version=version,
                    extra_params={"num_servers": num_servers, "tools_per_server": tools_per_server},
                )

                save_summary(summary=summary, artifact_dir=env.ARTIFACT_DIR, job_name=job_name)
                _capture_pod_logs(namespace=namespace, job_name=job_name)
                cleanup_locust_job(namespace=namespace, job_name=job_name)
                all_summaries.append(summary)

                if metrics_cfg.enabled:
                    raw_dir = capture_metrics(
                        namespaces=metrics_cfg.namespaces,
                        start_time=test_start_time,
                        end_time=test_end_time,
                        step_seconds=metrics_cfg.step_seconds,
                        queries=metrics_cfg.queries,
                        include_gpu=metrics_cfg.include_gpu,
                        artifact_dir=env.ARTIFACT_DIR,
                        job_name=job_name,
                    )
                    generate_plots(metrics_dir=raw_dir)

                mlflow_run_name = f"mcpgw-v{version}-s{num_servers}-u{users}"
                export_to_mlflow(run_name=mlflow_run_name)

        _cleanup_servers(namespace=namespace, num_servers=num_servers, mock_server=mock_server)

    write_json(
        env.ARTIFACT_DIR / "artifacts" / "results" / "test_summary.json",
        {
            "preset": preset,
            "version": version,
            "servers": servers,
            "concurrency": concurrency,
            "targets": targets,
            "tools_per_server": tools_per_server,
            "results": all_summaries,
        },
    )

    return 0


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
        apply_infra.apply_infrastructure(
            namespace=namespace,
            count=num_servers,
            api_group=api_group,
        )
        apply_infra.wait_for_registrations(
            namespace=namespace,
            count=num_servers,
            api_group=api_group,
        )


def _cleanup_servers(*, namespace: str, num_servers: int, mock_server: str) -> None:
    """Remove mock servers and infrastructure after a server-level iteration."""
    api_group = cfg.get_api_group()
    deploy_mock_servers.cleanup_servers(namespace=namespace)
    apply_infra.cleanup_infrastructure(namespace=namespace, api_group=api_group)


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------


def _run_test(*, users: int, target: str, num_servers: int) -> LocustResults:
    """Run a single Locust test."""
    locust_config = cfg.build_locust_config(
        users=users,
        target=target,
        num_servers=num_servers,
    )
    return run_locust.run(config=locust_config, artifact_dir=env.ARTIFACT_DIR, cleanup=False)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _capture_pod_logs(*, namespace: str, job_name: str) -> None:
    """Capture logs from all pods in test namespace and gateway infrastructure namespaces."""
    from projects.core.dsl.utils.k8s import capture_pod_logs

    logs_dir = env.ARTIFACT_DIR / "artifacts" / "results" / job_name / "pod_logs"
    capture_pod_logs(namespace=namespace, output_dir=logs_dir)

    for gw_ns in ("mcp-system", "gateway-system"):
        gw_logs_dir = logs_dir / gw_ns
        capture_pod_logs(namespace=gw_ns, output_dir=gw_logs_dir)
