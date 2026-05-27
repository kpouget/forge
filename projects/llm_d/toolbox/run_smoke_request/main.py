#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime
from projects.llm_d.toolbox import toolbox_helper


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

    llmd_runtime.init()
    context = execute_tasks(locals())
    return context.response


@task
def run_smoke_request_task(args, ctx):
    """Run the smoke request job and validate its response"""

    ctx.response = run_smoke_request(
        artifact_dir=args.artifact_dir,
        namespace=args.namespace,
        smoke=args.smoke,
        model=args.model,
        smoke_request=args.smoke_request,
        endpoint_url=args.endpoint_url,
    )
    return "Smoke request completed"


def run_smoke_request(
    *,
    artifact_dir: Path,
    namespace: str,
    smoke: dict,
    model: dict,
    smoke_request: dict,
    endpoint_url: str,
) -> dict[str, Any]:
    job_name = smoke["job_name"]

    payload = {
        "model": model["served_model_name"],
        "prompt": smoke_request["prompt"],
        "max_tokens": smoke_request["max_tokens"],
        "temperature": smoke_request["temperature"],
    }
    toolbox_helper.write_json(artifact_dir / "artifacts" / "smoke.request.json", payload)

    llmd_runtime.oc(
        "delete",
        "job",
        job_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )
    llmd_runtime.wait_until(
        f"job/{job_name} deletion in {namespace}",
        timeout_seconds=120,
        interval_seconds=5,
        predicate=lambda: not llmd_runtime.resource_exists("job", job_name, namespace=namespace),
    )

    llmd_runtime.apply_manifest(
        artifact_dir / "src" / "smoke-job.yaml",
        llmd_runtime.render_smoke_request_job_from_parts(
            namespace=namespace,
            smoke=smoke,
            endpoint_url=endpoint_url,
            payload=payload,
        ),
    )

    try:
        llmd_runtime.wait_for_job_completion(
            job_name,
            namespace,
            timeout_seconds=(
                smoke["request_retries"]
                * (smoke["request_timeout_seconds"] + smoke["request_retry_delay_seconds"])
            ),
            interval_seconds=5,
        )
    finally:
        capture_smoke_state(
            artifact_dir=artifact_dir,
            namespace=namespace,
            smoke=smoke,
        )

    result = llmd_runtime.oc(
        "logs",
        f"job/{job_name}",
        "-n",
        namespace,
        check=False,
        capture_output=True,
    )

    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(
            f"Smoke request job {job_name} completed but response logs could not be read: {result.stderr}"
        )

    response = json.loads(result.stdout)
    if not response.get("choices"):
        raise RuntimeError(f"Invalid smoke response payload: {result.stdout}")

    toolbox_helper.write_json(artifact_dir / "artifacts" / "smoke.response.json", response)
    return response


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
    result = llmd_runtime.oc(
        "logs",
        f"job/{job_name}",
        "-n",
        namespace,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        toolbox_helper.write_text(artifacts_dir / "smoke_job.logs", result.stdout)


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
    result = llmd_runtime.oc(*args, check=False, capture_output=True)
    if result.returncode == 0 and result.stdout:
        toolbox_helper.write_text(destination, result.stdout)


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
