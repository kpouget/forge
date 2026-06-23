#!/usr/bin/env python3
"""
Shared toolbox: run a distributed Locust load test on Kubernetes.

Handles the full lifecycle:
1. Create/update ConfigMap with test scripts
2. Template the distributed Job YAML (substitute __PLACEHOLDERS__)
3. Apply Job (master headless service + master Job + workers Job)
4. Wait for master Job completion
5. Collect CSV results from master pod logs
6. Clean up Jobs

This is a generic Locust runner — callers provide their own locustfiles,
template, and environment variables.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc, oc_get_json

logger = logging.getLogger(__name__)


@dataclass
class LocustResults:
    """Parsed results from a Locust run."""

    stats_csv: str
    stats_history_csv: str
    failures_csv: str
    raw_log: str


@entrypoint
def run(
    *,
    job_name: str,
    namespace: str,
    host_url: str,
    users: int,
    workers: int,
    duration_seconds: int,
    spawn_rate: int | None = None,
    locust_image: str = "locustio/locust:2.32.7",
    configmap_name: str = "locust-scripts",
    template_path: str | None = None,
    locustfiles_dir: str | None = None,
    locustfile_names: list[str] | None = None,
    extra_files: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    extra_volumes: list[dict] | None = None,
    extra_volume_mounts: list[dict] | None = None,
    job_timeout_seconds: int = 900,
    worker_startup_wait_seconds: int | None = None,
    node_selector: dict[str, str] | None = None,
    tolerations: list[dict[str, str]] | None = None,
    cleanup: bool = True,
) -> int:
    """Execute a full Locust distributed run and return parsed results."""
    return execute_tasks(locals())


@task
def create_configmap(args, ctx):
    """Create or replace the ConfigMap containing locust scripts."""
    configmap_name = args.configmap_name
    namespace = args.namespace
    locustfile_names = args.locustfile_names or ["locustfile.py"]
    locustfiles_dir = Path(args.locustfiles_dir) if args.locustfiles_dir else None
    extra_files = [Path(p) for p in args.extra_files] if args.extra_files else []
    labels = args.labels or {}

    logger.info("Creating ConfigMap %s in %s", configmap_name, namespace)

    oc(
        "delete",
        "configmap",
        configmap_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )

    from_file_args = []
    for filename in locustfile_names:
        filepath = locustfiles_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"{filename} not found at {filepath}")
        from_file_args.append(f"--from-file={filename}={filepath}")

    for extra_path in extra_files:
        if not extra_path.exists():
            raise FileNotFoundError(f"Extra file not found: {extra_path}")
        from_file_args.append(f"--from-file={extra_path.name}={extra_path}")

    oc(
        "create",
        "configmap",
        configmap_name,
        "-n",
        namespace,
        *from_file_args,
    )

    if labels:
        label_args = [f"{k}={v}" for k, v in labels.items()]
        oc(
            "label",
            "configmap",
            configmap_name,
            "-n",
            namespace,
            "--overwrite",
            *label_args,
            check=False,
        )

    return f"ConfigMap {configmap_name} created"


@task
def deploy_locust_job(args, ctx):
    """Template and apply the Locust distributed Job YAML."""
    import yaml as yaml_mod

    template = Path(args.template_path) if args.template_path else None
    if not template:
        template = Path(__file__).parent.parent.parent / "templates" / "locust_job.yaml"
    if not template.exists():
        raise FileNotFoundError(f"Locust template not found: {template}")

    spawn_rate = args.spawn_rate if args.spawn_rate else args.users
    locustfile_names = args.locustfile_names or ["locustfile.py"]

    logger.info(
        "Deploying Locust job %s: %d users, %d workers, host=%s",
        args.job_name,
        args.users,
        args.workers,
        args.host_url,
    )

    replacements = {
        "JOB_NAME": args.job_name,
        "NAMESPACE": args.namespace,
        "HOST_URL": args.host_url,
        "USERS": str(args.users),
        "DURATION": str(args.duration_seconds),
        "NUM_WORKERS": str(args.workers),
        "SPAWN_RATE": str(spawn_rate),
        "CONFIGMAP_NAME": args.configmap_name,
        "LOCUST_IMAGE": args.locust_image,
        "LOCUSTFILE": locustfile_names[0],
    }

    yaml_content = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        yaml_content = yaml_content.replace(f"__{key}__", value)

    docs = list(yaml_mod.safe_load_all(yaml_content))
    _inject_into_docs(docs, args)

    rendered_yaml = yaml_mod.dump_all(docs, sort_keys=False, default_flow_style=False)

    rendered_path = args.artifact_dir / "src" / "locust-job.yaml"
    rendered_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_path.write_text(rendered_yaml, encoding="utf-8")
    oc("apply", "-f", str(rendered_path))

    if args.worker_startup_wait_seconds is not None:
        wait = args.worker_startup_wait_seconds
    else:
        wait = 10 if args.workers > 4 else 5
    logger.info("Waiting %ds for %d worker(s) to connect", wait, args.workers)
    time.sleep(wait)

    return f"Locust job {args.job_name} deployed"


@retry(attempts=90, delay=10, backoff=1.0)
@task
def wait_for_completion(args, ctx):
    """Wait for the Locust master Job to complete."""
    master_job = f"{args.job_name}-master"

    payload = oc_get_json("job", name=master_job, namespace=args.namespace, ignore_not_found=True)
    if not payload:
        return (False, f"Waiting for job/{master_job} to appear")

    status = payload.get("status", {})
    if status.get("succeeded", 0):
        return f"job/{master_job} completed successfully"

    for condition in status.get("conditions", []):
        if condition.get("type") == "Failed" and condition.get("status") == "True":
            raise RuntimeError(f"job/{master_job} failed: {condition.get('reason', 'unknown')}")
    if status.get("failed", 0):
        raise RuntimeError(f"job/{master_job} failed after {status['failed']} attempt(s)")

    return (False, f"Waiting for job/{master_job} completion")


@task
def collect_results(args, ctx):
    """Parse CSV results from the master pod logs using delimiter markers."""
    master_job = f"{args.job_name}-master"
    logger.info("Collecting results from job/%s", master_job)

    result = oc("logs", f"job/{master_job}", "-n", args.namespace, log_stdout=False)
    raw_log = result.stdout

    stats_csv = _extract_section(raw_log, "===CSV_STATS===", "===CSV_STATS_HISTORY===")
    stats_history_csv = _extract_section(raw_log, "===CSV_STATS_HISTORY===", "===CSV_FAILURES===")
    failures_csv = _extract_section(raw_log, "===CSV_FAILURES===", "===CSV_END===")

    lines = stats_csv.strip().count("\n") + 1 if stats_csv.strip() else 0
    logger.info("Collected stats CSV: %d lines", lines)

    ctx.results = LocustResults(
        stats_csv=stats_csv,
        stats_history_csv=stats_history_csv,
        failures_csv=failures_csv,
        raw_log=raw_log,
    )

    return f"Collected results ({lines} stat lines)"


@task
def save_artifacts(args, ctx):
    """Save results to the artifact directory."""
    results = ctx.results
    results_dir = args.artifact_dir / "artifacts" / "results" / args.job_name
    results_dir.mkdir(parents=True, exist_ok=True)

    if results.stats_csv:
        (results_dir / "stats.csv").write_text(results.stats_csv, encoding="utf-8")
    if results.stats_history_csv:
        (results_dir / "stats_history.csv").write_text(results.stats_history_csv, encoding="utf-8")
    if results.failures_csv:
        (results_dir / "failures.csv").write_text(results.failures_csv, encoding="utf-8")
    (results_dir / "master.log").write_text(results.raw_log, encoding="utf-8")

    return f"Artifacts saved to {results_dir}"


@always
@task
def cleanup_job(args, ctx):
    """Remove the Locust master, workers, and headless service."""
    if not args.cleanup:
        return "Cleanup skipped (cleanup=False)"

    logger.info("Cleaning up Locust job %s", args.job_name)
    for resource in (
        f"job/{args.job_name}-master",
        f"job/{args.job_name}-workers",
        f"svc/{args.job_name}-master",
    ):
        oc(
            "delete",
            resource,
            "-n",
            args.namespace,
            "--ignore-not-found=true",
            check=False,
        )

    return f"Cleaned up {args.job_name}"


# ---------------------------------------------------------------------------
# Internal helpers (not tasks)
# ---------------------------------------------------------------------------


def _extract_section(text: str, start_marker: str, end_marker: str) -> str:
    """Extract text between two delimiter markers."""
    try:
        start_idx = text.index(start_marker) + len(start_marker)
        end_idx = text.index(end_marker, start_idx)
        return text[start_idx:end_idx].strip()
    except ValueError:
        return ""


def _inject_into_docs(docs: list[dict], args) -> None:
    """Inject labels, env vars, volumes, scheduling into all K8s docs."""
    env_vars = args.env_vars or {}
    labels = args.labels or {}
    extra_volumes = args.extra_volumes or []
    extra_volume_mounts = args.extra_volume_mounts or []

    env_list = [{"name": k, "value": v} for k, v in env_vars.items()]

    for doc in docs:
        if not doc:
            continue

        kind = doc.get("kind")

        if labels:
            doc.setdefault("metadata", {}).setdefault("labels", {}).update(labels)

        if kind != "Job":
            continue

        pod_spec = doc.get("spec", {}).get("template", {}).get("spec", {})
        if not pod_spec:
            continue

        if args.node_selector:
            pod_spec["nodeSelector"] = args.node_selector
        if args.tolerations:
            pod_spec["tolerations"] = args.tolerations

        if env_list:
            for container in pod_spec.get("containers", []):
                container.setdefault("env", []).extend(env_list)

        if extra_volumes:
            pod_spec.setdefault("volumes", []).extend(extra_volumes)
        if extra_volume_mounts:
            for container in pod_spec.get("containers", []):
                container.setdefault("volumeMounts", []).extend(extra_volume_mounts)
