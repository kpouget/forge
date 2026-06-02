#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import yaml

from projects.core.dsl import (
    always,
    execute_tasks,
    task,
    template,
    toolbox,
)

logger = logging.getLogger(__name__)


def _wait_until(description, *, timeout_seconds, interval_seconds, predicate):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except RuntimeError:
            raise
        except Exception as exc:
            logger.info("waiting for %s: %s", description, exc)
        time.sleep(interval_seconds)
    raise RuntimeError(f"Timed out waiting for {description}")


def _wait_for_job_completion(job_name, namespace, *, timeout_seconds, interval_seconds=10):
    def _job_terminal():
        payload = _oc_get_json("job", name=job_name, namespace=namespace)
        if payload is None:
            return None
        status = payload.get("status", {})
        if status.get("succeeded"):
            return payload
        for condition in status.get("conditions", []):
            if condition.get("type") == "Failed" and condition.get("status") == "True":
                raise RuntimeError(
                    f"job/{job_name} failed: {condition.get('reason', 'unknown')}"
                )
        if status.get("failed"):
            raise RuntimeError(f"job/{job_name} failed after {status['failed']} attempt(s)")
        return None

    return _wait_until(
        f"job/{job_name} completion in {namespace}",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        predicate=_job_terminal,
    )


def _oc_run(*args: str, check: bool = True, capture_output: bool = True,
            input_text: str | None = None, timeout_seconds: float = 300
            ) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["oc", *args],
        capture_output=capture_output,
        text=True,
        input=input_text,
        timeout=timeout_seconds,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"oc {' '.join(args)} failed: {result.stderr}")
    return result


def _oc_get_json(kind: str, *, name: str | None = None, namespace: str | None = None,
                 selector: str | None = None, ignore_not_found: bool = False
                 ) -> dict | None:
    cmd = ["get", kind]
    if name:
        cmd.append(name)
    if namespace:
        cmd.extend(["-n", namespace])
    if selector:
        cmd.extend(["-l", selector])
    cmd.extend(["-o", "json"])
    result = _oc_run(*cmd, check=False)
    if result.returncode != 0:
        if ignore_not_found:
            return None
        raise RuntimeError(f"oc get {kind} failed: {result.stderr}")
    return json.loads(result.stdout)


def _best_effort_delete(description: str, *oc_args: str) -> None:
    try:
        _oc_run(*oc_args, check=False, timeout_seconds=60)
    except subprocess.TimeoutExpired:
        logger.warning("Timed out deleting %s", description)


def _apply_manifest(artifact_path: Path, manifest: dict) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    _oc_run("apply", "-f", str(artifact_path))


def _write_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _render_pvc(*, name: str, namespace: str, pvc_size: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "rhaiis",
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": pvc_size}},
        },
    }


def _render_copy_pod(*, name: str, namespace: str, image: str,
                     pvc_name: str, node_name: str | None = None) -> dict:
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "rhaiis",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "copy-helper",
                    "image": image,
                    "command": ["/bin/sleep", "300"],
                    "volumeMounts": [
                        {"name": "results", "mountPath": "/results"},
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "results",
                    "persistentVolumeClaim": {"claimName": pvc_name},
                }
            ],
        },
    }
    if node_name:
        pod["spec"]["nodeName"] = node_name
    return pod


def run(
    *,
    namespace: str,
    deployment_name: str,
    endpoint_url: str,
    model_id: str,
    data: str,
    rates: str = "1",
    max_seconds: int = 180,
    benchmark_image: str = "ghcr.io/vllm-project/guidellm:v0.6.0",
    backend_type: str = "openai_http",
    rate_type: str = "concurrent",
    timeout: int = 900,
    pvc_size: str = "5Gi",
):
    return execute_tasks(locals())


@task
def cleanup_previous_resources(args, context):
    context.job_name = f"guidellm-{args.deployment_name}"
    context.pvc_name = context.job_name
    context.copy_pod_name = f"{context.job_name}-copy"

    _best_effort_delete(
        "previous benchmark job and PVC",
        "delete", "job,pvc", context.job_name,
        "-n", args.namespace, "--ignore-not-found=true",
    )
    _best_effort_delete(
        "previous copy pod",
        "delete", "pod", context.copy_pod_name,
        "-n", args.namespace, "--ignore-not-found=true",
    )
    return f"Cleaned up previous resources for {context.job_name}"


@task
def create_benchmark_resources(args, context):
    _apply_manifest(
        args.artifact_dir / "src" / "guidellm-pvc.yaml",
        _render_pvc(
            name=context.pvc_name,
            namespace=args.namespace,
            pvc_size=args.pvc_size,
        ),
    )

    output_path = args.artifact_dir / "src" / "guidellm-job.yaml"
    template.render_template_to_file("guidellm_job.yaml.j2", output_path)
    _oc_run("apply", "-f", str(output_path))

    return f"Created PVC and benchmark job {context.job_name}"


@task
def wait_for_completion(args, context):
    _wait_for_job_completion(
        context.job_name,
        args.namespace,
        timeout_seconds=args.timeout,
    )
    return f"Benchmark job {context.job_name} completed successfully"


@always
@task
def capture_benchmark_state(args, context):
    artifacts_dir = args.artifact_dir / "artifacts"

    result = _oc_run("get", "job", context.job_name,
                     "-n", args.namespace, "-o", "yaml", check=False)
    if result.returncode == 0 and result.stdout:
        _write_artifact(artifacts_dir / "guidellm_job.yaml", result.stdout)

    result = _oc_run("get", "pods", "-l", f"job-name={context.job_name}",
                     "-n", args.namespace, "-o", "yaml", check=False)
    if result.returncode == 0 and result.stdout:
        _write_artifact(artifacts_dir / "guidellm_pods.yaml", result.stdout)

    result = _oc_run("logs", f"job/{context.job_name}",
                     "-n", args.namespace, check=False)
    if result.returncode == 0 and result.stdout:
        _write_artifact(artifacts_dir / "guidellm_benchmark.log", result.stdout)

    return "Captured benchmark state and logs"


@always
@task
def copy_benchmark_results(args, context):
    pod_data = _oc_get_json(
        "pods",
        namespace=args.namespace,
        selector=f"job-name={context.job_name}",
        ignore_not_found=True,
    )
    node_name = None
    if pod_data and pod_data.get("items"):
        node_name = pod_data["items"][0].get("spec", {}).get("nodeName")

    _apply_manifest(
        args.artifact_dir / "src" / "guidellm-copy-pod.yaml",
        _render_copy_pod(
            name=context.copy_pod_name,
            namespace=args.namespace,
            image=args.benchmark_image,
            pvc_name=context.pvc_name,
            node_name=node_name,
        ),
    )

    def _helper_ready():
        payload = _oc_get_json("pod", name=context.copy_pod_name, namespace=args.namespace)
        if payload is None:
            return False
        conditions = payload.get("status", {}).get("conditions", [])
        return any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in conditions
        )

    _wait_until(
        f"copy pod {context.copy_pod_name}",
        timeout_seconds=120,
        interval_seconds=5,
        predicate=_helper_ready,
    )

    result = _oc_run(
        "exec", "-n", args.namespace, context.copy_pod_name,
        "--", "cat", "/results/benchmarks.json",
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        _write_artifact(
            args.artifact_dir / "artifacts" / "results" / "benchmarks.json",
            result.stdout,
        )
        return "Copied benchmark results to artifacts"

    return "No benchmark results found on PVC"


@always
@task
def cleanup_benchmark_resources(args, context):
    _best_effort_delete(
        "benchmark job and PVC",
        "delete", "job,pvc", context.job_name,
        "-n", args.namespace, "--ignore-not-found=true",
    )
    _best_effort_delete(
        "copy pod",
        "delete", "pod", context.copy_pod_name,
        "-n", args.namespace, "--ignore-not-found=true",
    )
    return f"Cleaned up {context.job_name} resources"


main = toolbox.create_toolbox_main(run)

if __name__ == "__main__":
    main()
