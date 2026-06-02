#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils import write_json, write_text
from projects.core.dsl.utils.k8s import (
    apply_manifest,
    oc,
    resource_exists,
    wait_for_job_completion,
)
from projects.guidellm.toolbox.run_smoke_request.utils import render_smoke_request_job_from_parts


@entrypoint
def run(
    *,
    namespace: str,
    smoke: dict,
    model: dict,
    smoke_request: dict,
    endpoint_url: str,
) -> dict[str, object]:
    """
    Run the llm_d smoke request job against a resolved endpoint.

    Args:
        namespace: Namespace used by llm_d
        smoke: Smoke configuration block
        model: Selected model configuration
        smoke_request: Smoke-request configuration
        endpoint_url: Gateway endpoint URL returned by the deploy command
    """

    context = execute_tasks(locals())
    return context.response


@task
def prepare_smoke_request(args, ctx):
    """Prepare smoke request payload"""

    job_name = args.smoke["job_name"]
    ctx.job_name = job_name

    payload = {
        "model": args.model["served_model_name"],
        "prompt": args.smoke_request["prompt"],
        "max_tokens": args.smoke_request["max_tokens"],
        "temperature": args.smoke_request["temperature"],
    }
    ctx.payload = payload
    write_json(args.artifact_dir / "artifacts" / "smoke.request.json", payload)
    return f"Prepared smoke request for job {job_name}"


@task
def delete_existing_smoke_job(args, ctx):
    """Delete existing smoke job"""

    oc(
        "delete",
        "job",
        ctx.job_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )
    return f"Initiated deletion of job {ctx.job_name}"


@retry(attempts=24, delay=5, backoff=1.0)
@task
def wait_smoke_job_deleted(args, ctx):
    """Wait for smoke job deletion to complete"""

    if not resource_exists("job", ctx.job_name, namespace=args.namespace):
        return f"Job {ctx.job_name} deleted"
    return False  # Retry


@task
def create_smoke_job(args, ctx):
    """Create the smoke request job"""

    apply_manifest(
        args.artifact_dir / "src" / "smoke-job.yaml",
        render_smoke_request_job_from_parts(
            namespace=args.namespace,
            smoke=args.smoke,
            endpoint_url=args.endpoint_url,
            payload=ctx.payload,
        ),
    )
    return f"Created smoke job {ctx.job_name}"


@retry(attempts=60, delay=5, backoff=1.0)
@task
def wait_smoke_job_completion(args, ctx):
    """Wait for smoke job completion"""

    try:
        wait_for_job_completion(
            ctx.job_name,
            args.namespace,
            timeout_seconds=(
                args.smoke["request_retries"]
                * (
                    args.smoke["request_timeout_seconds"]
                    + args.smoke["request_retry_delay_seconds"]
                )
            ),
            interval_seconds=5,
        )
        return f"Smoke job {ctx.job_name} completed"
    except Exception:
        return False  # Retry


@task
def capture_smoke_response(args, ctx):
    """Capture and validate smoke response"""

    try:
        capture_smoke_state(
            artifact_dir=args.artifact_dir,
            namespace=args.namespace,
            smoke=args.smoke,
        )

        result = oc(
            "logs",
            f"job/{ctx.job_name}",
            "-n",
            args.namespace,
            check=False,
            capture_output=True,
        )

        if result.returncode != 0 or not result.stdout:
            raise RuntimeError(
                f"Smoke request job {ctx.job_name} completed but response logs could not be read: {result.stderr}"
            )

        response = json.loads(result.stdout)
        if not response.get("choices"):
            raise RuntimeError(f"Invalid smoke response payload: {result.stdout}")

        ctx.response = response
        write_json(args.artifact_dir / "artifacts" / "smoke.response.json", response)
        return f"Captured smoke response for job {ctx.job_name}"

    finally:
        capture_smoke_state(
            artifact_dir=args.artifact_dir,
            namespace=args.namespace,
            smoke=args.smoke,
        )


def capture_smoke_state(*, artifact_dir: Path, namespace: str, smoke: dict) -> None:
    job_name = smoke["job_name"]
    artifacts_dir = artifact_dir / "artifacts"

    capture_get("job", job_name, namespace, "yaml", artifacts_dir / "smoke_job.yaml")
    capture_get(
        "pods",
        None,
        namespace,
        "yaml",
        artifacts_dir / "smoke_job.pods.yaml",
        selector=f"job-name={job_name}",
    )
    result = oc(
        "logs",
        f"job/{job_name}",
        "-n",
        namespace,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        write_text(artifacts_dir / "smoke_job.logs", result.stdout)


def capture_get(
    kind: str,
    name: str | None,
    namespace: str,
    output: str,
    destination: Path,
    *,
    selector: str | None = None,
) -> None:
    args = ["get", kind]
    if name:
        args.append(name)
    args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    args.extend(["-o", output])
    result = oc(*args, check=False, capture_output=True)
    if result.returncode == 0 and result.stdout:
        write_text(destination, result.stdout)


if __name__ == "__main__":
    run.main()
