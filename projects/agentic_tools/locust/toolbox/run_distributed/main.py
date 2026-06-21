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
from dataclasses import dataclass, field
from pathlib import Path

from projects.core.dsl.utils.k8s import oc, oc_get_json

logger = logging.getLogger(__name__)


@dataclass
class LocustResults:
    """Parsed results from a Locust run."""

    stats_csv: str
    stats_history_csv: str
    failures_csv: str
    raw_log: str


@dataclass
class LocustRunConfig:
    """All parameters needed for a single distributed Locust run."""

    job_name: str
    namespace: str
    host_url: str
    users: int
    workers: int
    duration_seconds: int
    spawn_rate: int | None = None
    locust_image: str = "locustio/locust:2.32.7"
    configmap_name: str = "locust-scripts"
    template_path: Path | None = None
    locustfiles_dir: Path | None = None
    locustfile_names: list[str] = field(default_factory=lambda: ["locustfile.py"])
    extra_files: list[Path] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    extra_volumes: list[dict] = field(default_factory=list)
    extra_volume_mounts: list[dict] = field(default_factory=list)
    job_timeout_seconds: int = 900
    worker_startup_wait_seconds: int | None = None
    node_selector: dict[str, str] | None = None
    tolerations: list[dict[str, str]] | None = None


def run(
    *, config: LocustRunConfig, artifact_dir: Path | None = None, cleanup: bool = True
) -> LocustResults:
    """Execute a full Locust distributed run and return parsed results."""
    _create_configmap(config)
    _deploy_locust_job(config, artifact_dir=artifact_dir)
    _wait_for_completion(config)
    results = _collect_results(config)

    if artifact_dir:
        _save_artifacts(results=results, artifact_dir=artifact_dir, job_name=config.job_name)

    if cleanup:
        cleanup_job(namespace=config.namespace, job_name=config.job_name)
    return results


def _create_configmap(config: LocustRunConfig) -> None:
    """Create or replace the ConfigMap containing locust scripts."""
    logger.info("Creating ConfigMap %s in %s", config.configmap_name, config.namespace)

    oc(
        "delete",
        "configmap",
        config.configmap_name,
        "-n",
        config.namespace,
        "--ignore-not-found=true",
        check=False,
    )

    from_file_args = []
    for filename in config.locustfile_names:
        filepath = config.locustfiles_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"{filename} not found at {filepath}")
        from_file_args.append(f"--from-file={filename}={filepath}")

    for extra_path in config.extra_files:
        if not extra_path.exists():
            raise FileNotFoundError(f"Extra file not found: {extra_path}")
        from_file_args.append(f"--from-file={extra_path.name}={extra_path}")

    oc(
        "create",
        "configmap",
        config.configmap_name,
        "-n",
        config.namespace,
        *from_file_args,
    )

    if config.labels:
        label_args = [f"{k}={v}" for k, v in config.labels.items()]
        oc(
            "label",
            "configmap",
            config.configmap_name,
            "-n",
            config.namespace,
            "--overwrite",
            *label_args,
            check=False,
        )


def _deploy_locust_job(config: LocustRunConfig, *, artifact_dir: Path | None = None) -> None:
    """Template and apply the Locust distributed Job YAML."""
    import yaml as yaml_mod

    template = config.template_path
    if not template:
        template = Path(__file__).parent.parent.parent / "templates" / "locust_job.yaml"
    if not template.exists():
        raise FileNotFoundError(f"Locust template not found: {template}")

    logger.info(
        "Deploying Locust job %s: %d users, %d workers, host=%s",
        config.job_name,
        config.users,
        config.workers,
        config.host_url,
    )

    spawn_rate = config.spawn_rate if config.spawn_rate else config.users

    replacements = {
        "JOB_NAME": config.job_name,
        "NAMESPACE": config.namespace,
        "HOST_URL": config.host_url,
        "USERS": str(config.users),
        "DURATION": str(config.duration_seconds),
        "NUM_WORKERS": str(config.workers),
        "SPAWN_RATE": str(spawn_rate),
        "CONFIGMAP_NAME": config.configmap_name,
        "LOCUST_IMAGE": config.locust_image,
        "LOCUSTFILE": config.locustfile_names[0],
    }

    yaml_content = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        yaml_content = yaml_content.replace(f"__{key}__", value)

    docs = list(yaml_mod.safe_load_all(yaml_content))
    _inject_into_docs(docs, config)

    rendered_yaml = yaml_mod.dump_all(docs, sort_keys=False, default_flow_style=False)

    if artifact_dir:
        rendered_path = artifact_dir / "src" / "locust-job.yaml"
    else:
        import tempfile

        rendered_path = Path(tempfile.mkdtemp()) / "locust-job.yaml"
    rendered_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_path.write_text(rendered_yaml, encoding="utf-8")
    oc("apply", "-f", str(rendered_path))

    if config.worker_startup_wait_seconds is not None:
        wait = config.worker_startup_wait_seconds
    else:
        wait = 10 if config.workers > 4 else 5
    logger.info("Waiting %ds for %d worker(s) to connect", wait, config.workers)
    time.sleep(wait)


def _wait_for_completion(config: LocustRunConfig) -> None:
    """Wait for the Locust master Job to complete."""
    master_job = f"{config.job_name}-master"

    logger.info(
        "Waiting for job/%s completion (timeout=%ds)", master_job, config.job_timeout_seconds
    )

    deadline = time.time() + config.job_timeout_seconds
    while time.time() < deadline:
        payload = oc_get_json(
            "job", name=master_job, namespace=config.namespace, ignore_not_found=True
        )
        if payload:
            status = payload.get("status", {})
            if status.get("succeeded", 0):
                logger.info("job/%s completed successfully", master_job)
                return
            for condition in status.get("conditions", []):
                if condition.get("type") == "Failed" and condition.get("status") == "True":
                    raise RuntimeError(
                        f"job/{master_job} failed: {condition.get('reason', 'unknown')}"
                    )
            if status.get("failed", 0):
                raise RuntimeError(f"job/{master_job} failed after {status['failed']} attempt(s)")
        time.sleep(10)

    raise RuntimeError(f"Timed out waiting for job/{master_job} completion")


def _collect_results(config: LocustRunConfig) -> LocustResults:
    """Parse CSV results from the master pod logs using delimiter markers."""
    master_job = f"{config.job_name}-master"
    logger.info("Collecting results from job/%s", master_job)

    result = oc("logs", f"job/{master_job}", "-n", config.namespace)
    raw_log = result.stdout

    stats_csv = _extract_section(raw_log, "===CSV_STATS===", "===CSV_STATS_HISTORY===")
    stats_history_csv = _extract_section(raw_log, "===CSV_STATS_HISTORY===", "===CSV_FAILURES===")
    failures_csv = _extract_section(raw_log, "===CSV_FAILURES===", "===CSV_END===")

    lines = stats_csv.strip().count("\n") + 1 if stats_csv.strip() else 0
    logger.info("Collected stats CSV: %d lines", lines)

    return LocustResults(
        stats_csv=stats_csv,
        stats_history_csv=stats_history_csv,
        failures_csv=failures_csv,
        raw_log=raw_log,
    )


def _extract_section(text: str, start_marker: str, end_marker: str) -> str:
    """Extract text between two delimiter markers."""
    try:
        start_idx = text.index(start_marker) + len(start_marker)
        end_idx = text.index(end_marker, start_idx)
        return text[start_idx:end_idx].strip()
    except ValueError:
        return ""


def _save_artifacts(*, results: LocustResults, artifact_dir: Path, job_name: str) -> None:
    """Save results to the artifact directory."""
    results_dir = artifact_dir / "artifacts" / "results" / job_name
    results_dir.mkdir(parents=True, exist_ok=True)

    if results.stats_csv:
        (results_dir / "stats.csv").write_text(results.stats_csv, encoding="utf-8")
    if results.stats_history_csv:
        (results_dir / "stats_history.csv").write_text(results.stats_history_csv, encoding="utf-8")
    if results.failures_csv:
        (results_dir / "failures.csv").write_text(results.failures_csv, encoding="utf-8")
    (results_dir / "master.log").write_text(results.raw_log, encoding="utf-8")

    logger.info("Artifacts saved to %s", results_dir)


def cleanup_job(*, namespace: str, job_name: str) -> None:
    """Remove the Locust master, workers, and headless service."""
    logger.info("Cleaning up Locust job %s", job_name)
    for resource in (
        f"job/{job_name}-master",
        f"job/{job_name}-workers",
        f"svc/{job_name}-master",
    ):
        oc(
            "delete",
            resource,
            "-n",
            namespace,
            "--ignore-not-found=true",
            check=False,
        )


def _inject_into_docs(docs: list[dict], config: LocustRunConfig) -> None:
    """Inject labels, env vars, volumes, scheduling into all K8s docs."""
    env_list = [{"name": k, "value": v} for k, v in config.env_vars.items()]

    for doc in docs:
        if not doc:
            continue

        kind = doc.get("kind")

        # Inject labels into metadata of all resources
        if config.labels:
            doc.setdefault("metadata", {}).setdefault("labels", {}).update(config.labels)

        if kind != "Job":
            continue

        pod_spec = doc.get("spec", {}).get("template", {}).get("spec", {})
        if not pod_spec:
            continue

        # Scheduling
        if config.node_selector:
            pod_spec["nodeSelector"] = config.node_selector
        if config.tolerations:
            pod_spec["tolerations"] = config.tolerations

        # Env vars — inject into all containers
        if env_list:
            for container in pod_spec.get("containers", []):
                container.setdefault("env", []).extend(env_list)

        # Extra volumes + mounts
        if config.extra_volumes:
            pod_spec.setdefault("volumes", []).extend(config.extra_volumes)
        if config.extra_volume_mounts:
            for container in pod_spec.get("containers", []):
                container.setdefault("volumeMounts", []).extend(config.extra_volume_mounts)
