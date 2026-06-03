#!/usr/bin/env python3

from __future__ import annotations

import json

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils import write_json, write_text
from projects.core.dsl.utils.k8s import (
    oc,
    oc_apply,
    oc_get_json,
    oc_resource_exists,
)
from projects.guidellm.toolbox.run_smoke_request.utils import render_smoke_request_pod_from_parts


@entrypoint
def run(
    *,
    namespace: str,
    endpoint_url: str,
    pod_name: str = "llm-d-smoke",
    client_image: str = "curlimages/curl:8.11.1",
    endpoint_path: str = "/v1/completions",
    request_timeout_seconds: int = 60,
    served_model_name: str,
    prompt: str = "San Francisco is a",
    max_tokens: int = 50,
    temperature: float = 0.7,
) -> dict[str, object]:
    """
    Run the llm_d smoke request against a resolved endpoint using a pod.

    Args:
        namespace: Namespace used by llm_d
        endpoint_url: Gateway endpoint URL returned by the deploy command
        pod_name: Name for the smoke test pod
        client_image: Container image for making HTTP requests
        endpoint_path: API endpoint path to test
        request_timeout_seconds: Timeout for the request
        served_model_name: Model name to use in API requests
        prompt: Test prompt to send
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
    """

    context = execute_tasks(locals())
    return context.response


@task
def validate_endpoint(args, ctx):
    """Validate that endpoint_url is provided and not None"""

    if not args.endpoint_url:
        raise ValueError("endpoint_url cannot be None or empty")

    return f"Validated endpoint URL: {args.endpoint_url}"


@task
def prepare_smoke_request(args, ctx):
    """Prepare smoke request payload"""

    ctx.pod_name = args.pod_name

    payload = {
        "model": args.served_model_name,
        "prompt": args.prompt,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }
    ctx.payload = payload

    # Ensure artifacts directory exists
    artifacts_dir = args.artifact_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    write_json(artifacts_dir / "smoke.request.json", payload)
    return f"Prepared smoke request for pod {args.pod_name}"


@task
def delete_existing_smoke_pod(args, ctx):
    """Delete existing smoke pod"""

    oc(
        "delete",
        "pod",
        ctx.pod_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )
    return f"Initiated deletion of pod {ctx.pod_name}"


@retry(attempts=24, delay=5, backoff=1.0)
@task
def wait_smoke_pod_deleted(args, ctx):
    """Wait for smoke pod deletion to complete"""

    if not oc_resource_exists("pod", ctx.pod_name, namespace=args.namespace):
        return f"Pod {ctx.pod_name} deleted"
    return False  # Retry


@task
def create_smoke_pod(args, ctx):
    """Create the smoke request pod"""

    # Ensure the src directory exists
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    oc_apply(
        src_dir / "smoke-pod.yaml",
        render_smoke_request_pod_from_parts(
            namespace=args.namespace,
            pod_name=args.pod_name,
            client_image=args.client_image,
        ),
    )
    return f"Created smoke pod {ctx.pod_name}"


@retry(attempts=60, delay=5, backoff=1.0)
@task
def wait_smoke_pod_running(args, ctx):
    """Wait for smoke pod to be running"""

    # Check pod status
    payload = oc_get_json(
        "pod",
        name=ctx.pod_name,
        namespace=args.namespace,
        ignore_not_found=True,
    )
    if not payload:
        return (False, f"Pod {ctx.pod_name} not found, retrying...")

    phase = payload.get("status", {}).get("phase")
    if phase == "Running":
        return f"Smoke pod {ctx.pod_name} is running"
    elif phase in ["Failed", "Succeeded"]:
        raise RuntimeError(f"Pod {ctx.pod_name} entered unexpected phase: {phase}")

    # Still pending
    return (False, f"Smoke pod {ctx.pod_name} still in phase: {phase or 'Unknown'}, retrying...")


@task
def execute_smoke_request(args, ctx):
    """Execute the curl command inside the running pod"""

    # Build the curl command
    curl_cmd = [
        "curl",
        "-k",
        "-sSf",
        "--max-time",
        str(args.request_timeout_seconds),
        f"{args.endpoint_url}{args.endpoint_path}",
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps(ctx.payload),
    ]

    # Execute the command in the pod (single attempt)
    result = oc(
        "exec",
        ctx.pod_name,
        "-n",
        args.namespace,
        "-c",
        "smoke",
        "--",
        *curl_cmd,
        check=False,
        capture_output=True,
    )

    # Save stdout and stderr for debugging
    if result.stdout:
        write_text(args.artifact_dir / "artifacts" / "smoke.curl.stdout", result.stdout)
    if result.stderr:
        write_text(args.artifact_dir / "artifacts" / "smoke.curl.stderr", result.stderr)

    if result.returncode == 0 and result.stdout:
        try:
            response = json.loads(result.stdout)
            if response.get("choices"):
                ctx.response = response
                write_json(args.artifact_dir / "artifacts" / "smoke.response.json", response)
                return "Smoke request completed successfully"
            else:
                raise ValueError(f"Invalid response format: {result.stdout}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON response: {result.stdout}") from e
    else:
        # Request failed
        raise RuntimeError(
            f"Smoke request failed (exit {result.returncode}): {result.stderr or 'No error message'}"
        )


@task
def capture_smoke_state(args, ctx):
    """Capture the smoke pod state for debugging"""

    artifacts_dir = args.artifact_dir / "artifacts"

    # Capture pod YAML
    result = oc(
        "get",
        "pod",
        ctx.pod_name,
        "-n",
        args.namespace,
        "-o",
        "yaml",
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        write_text(artifacts_dir / "smoke_pod.yaml", result.stdout)

    # Capture pod logs
    result = oc(
        "logs",
        ctx.pod_name,
        "-n",
        args.namespace,
        "-c",
        "smoke",
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        write_text(artifacts_dir / "smoke_pod.logs", result.stdout)

    return f"Captured smoke pod state for {ctx.pod_name}"


if __name__ == "__main__":
    run.main()
