"""
Test phase for Llama Stack performance benchmarks.

Each test iteration is fully self-contained:
  deploy stack → run locust → collect results → cleanup

The sweep is simply:
  for replicas in replica_levels:
      for users in concurrency_levels:
          deploy → test → collect → cleanup
  export all to MLflow
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from projects.agentic_tools.locust.toolbox.export_to_mlflow.main import (
    export_to_mlflow as _shared_export_to_mlflow,
)
from projects.agentic_tools.locust.toolbox.export_to_mlflow.main import (
    generate_summary,
)
from projects.agentic_tools.locust.toolbox.export_to_mlflow.main import (
    save_summary as _shared_save_summary,
)
from projects.agentic_tools.locust.toolbox.parse_results.main import parse_stats_csv
from projects.agentic_tools.locust.toolbox.run_distributed import main as run_locust
from projects.agentic_tools.locust.toolbox.run_distributed.main import LocustResults
from projects.caliper.metrics.capture import capture_metrics
from projects.caliper.metrics.config import MetricsCaptureConfig
from projects.caliper.metrics.plot import generate_plots
from projects.core.dsl.utils import write_json
from projects.core.dsl.utils.k8s import oc
from projects.core.library import env
from projects.llamastack.orchestration.runtime_config import cfg

logger = logging.getLogger(__name__)


def run() -> int:
    """For each replica × users combination: deploy → test → collect → export → cleanup."""
    namespace = cfg.get_namespace()
    preset = cfg.get_preset_name()
    user_class = cfg.get_user_class()
    replica_levels = cfg.get_replica_levels()
    concurrency_levels = cfg.get_concurrency_levels()
    warmup_seconds = cfg.get_warmup_seconds()
    metrics_cfg = MetricsCaptureConfig(**cfg.get_metrics_config())

    total = len(replica_levels) * len(concurrency_levels)
    logger.info(
        "Test matrix: %d replicas × %d concurrency = %d jobs",
        len(replica_levels),
        len(concurrency_levels),
        total,
    )

    all_summaries = []
    for replicas in replica_levels:
        for users in concurrency_levels:
            job_name = f"ls-{preset}-r{replicas}-u{users}"[:63]
            logger.info("[%s] replicas=%d users=%d", job_name, replicas, users)

            _deploy_stack(namespace=namespace, replicas=replicas)

            run_start_time = datetime.now(UTC)
            results = _run_locust(namespace=namespace, users=users, job_name=job_name)
            test_end_time = datetime.now(UTC)
            test_start_time = run_start_time + timedelta(seconds=warmup_seconds)

            metrics = parse_stats_csv(results.stats_csv)
            summary = generate_summary(
                metrics=metrics,
                preset=preset,
                target="llamastack",
                users=users,
                user_class=user_class,
                extra_params={"replicas": replicas},
            )
            _shared_save_summary(summary=summary, artifact_dir=env.ARTIFACT_DIR, job_name=job_name)
            _capture_pod_logs(namespace=namespace, job_name=job_name)

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

            all_summaries.append(summary)
            _shared_export_to_mlflow(run_name=job_name)
            _cleanup_stack(namespace=namespace)

    write_json(
        env.ARTIFACT_DIR / "artifacts" / "results" / "sweep_summary.json",
        {
            "preset": preset,
            "replica_levels": replica_levels,
            "concurrency_levels": concurrency_levels,
            "results": all_summaries,
        },
    )
    return 0


# ---------------------------------------------------------------------------
# Deploy / Cleanup
# ---------------------------------------------------------------------------


def _deploy_stack(*, namespace: str, replicas: int) -> None:
    """Deploy test stack based on experiment type.

    lls_overhead: postgres + llamastack + optional MCP server + prompts
    rhaiis_direct: prompts only (vLLM already deployed by prepare phase)
    """
    experiment_type = cfg.get_experiment_type()
    model_config = cfg.get_model_config()

    if experiment_type == "lls_overhead":
        from projects.llamastack.toolbox.deploy_llamastack import main as deploy_llamastack_mod
        from projects.llamastack.toolbox.deploy_postgres import main as deploy_postgres_mod

        deploy_postgres_mod.run(namespace=namespace)

        if cfg.get_deploy_mcp_server():
            from projects.agentic_tools.mcp.toolbox.deploy_tokenized_mcp_server import (
                main as deploy_tokenized,
            )

            deploy_tokenized.run(
                namespace=namespace,
                name="benchmark-mcp-server",
                num_tools=1,
                tool_response_tokens=100,
                tokenizer_model=model_config["tokenizer"],
                labels={"forge.openshift.io/project": "llamastack"},
            )

        deploy_llamastack_mod.run(
            namespace=namespace,
            distribution_name=cfg.get_distribution_name(),
            model_name=model_config["name"],
            replicas=replicas,
            disable_otel=cfg.get_disable_otel(),
            enable_hpa=cfg.get_enable_hpa(),
            hpa_config=cfg.get_hpa_config(),
        )

    input_tokens = cfg.get_input_tokens()
    if input_tokens > 0:
        _generate_prompts(namespace=namespace, input_tokens=input_tokens)


def _cleanup_stack(*, namespace: str) -> None:
    """Clean up iteration resources (not vLLM)."""
    experiment_type = cfg.get_experiment_type()

    if experiment_type == "lls_overhead":
        from projects.llamastack.toolbox.cleanup_test_resources import main as cleanup_mod

        cleanup_mod.run(
            namespace=namespace,
            distribution_name=cfg.get_distribution_name(),
            model_name=cfg.get_model_config()["name"],
            cleanup_inference=False,
        )

    oc(
        "delete",
        "configmap",
        "synthetic-prompts",
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )


# ---------------------------------------------------------------------------
# Locust
# ---------------------------------------------------------------------------


def _run_locust(*, namespace: str, users: int, job_name: str) -> LocustResults:
    """Run distributed Locust test."""
    locust_config = cfg.build_locust_config(
        namespace=namespace,
        users=users,
        job_name=job_name,
    )
    return run_locust.run(config=locust_config, artifact_dir=env.ARTIFACT_DIR, cleanup=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_prompts(*, namespace: str, input_tokens: int) -> None:
    from projects.agentic_tools.locust.toolbox.generate_prompts import main as generate_prompts_mod

    model_config = cfg.get_model_config()
    users = cfg.get_users()

    generate_prompts_mod.run(
        namespace=namespace,
        num_tokens=input_tokens,
        num_prompts=max(users * 2, 50),
        tokenizer_model=model_config["tokenizer"],
    )


def _capture_pod_logs(*, namespace: str, job_name: str) -> None:
    from projects.core.dsl.utils.k8s import capture_pod_logs

    logs_dir = env.ARTIFACT_DIR / "artifacts" / "results" / job_name / "pod_logs"
    capture_pod_logs(namespace=namespace, output_dir=logs_dir)
