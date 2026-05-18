#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime, phase_inputs


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

    config = phase_inputs.build_test_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=".",
        preset_name="run-smoke-request",
        namespace=args.namespace,
        platform={"smoke": args.smoke},
        model_key="unused",
        model=args.model,
        scheduler_profile_key="default",
        scheduler_profile=None,
        model_cache={},
        smoke_request=args.smoke_request,
        benchmark=None,
    )
    ctx.response = run_smoke_request(config, args.endpoint_url)
    return "Smoke request completed"


def run_smoke_request(config: phase_inputs.TestInputs, endpoint_url: str) -> dict[str, Any]:
    namespace = config.namespace
    job_name = config.platform["smoke"]["job_name"]

    payload = {
        "model": config.model["served_model_name"],
        "prompt": config.smoke_request["prompt"],
        "max_tokens": config.smoke_request["max_tokens"],
        "temperature": config.smoke_request["temperature"],
    }
    llmd_runtime.write_json(config.artifact_dir / "artifacts" / "smoke.request.json", payload)

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
        config.artifact_dir / "src" / "smoke-job.yaml",
        llmd_runtime.render_smoke_request_job(config, endpoint_url, payload),
    )

    try:
        llmd_runtime.wait_for_job_completion(
            job_name,
            namespace,
            timeout_seconds=(
                config.platform["smoke"]["request_retries"]
                * (
                    config.platform["smoke"]["request_timeout_seconds"]
                    + config.platform["smoke"]["request_retry_delay_seconds"]
                )
            ),
            interval_seconds=5,
        )
    finally:
        capture_smoke_state(config)

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

    llmd_runtime.write_json(config.artifact_dir / "artifacts" / "smoke.response.json", response)
    return response


def capture_smoke_state(config: phase_inputs.TestInputs) -> None:
    job_name = config.platform["smoke"]["job_name"]
    namespace = config.namespace
    artifacts_dir = config.artifact_dir / "artifacts"

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
        llmd_runtime.write_text(artifacts_dir / "smoke_job.logs", result.stdout)


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
        llmd_runtime.write_text(destination, result.stdout)


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
