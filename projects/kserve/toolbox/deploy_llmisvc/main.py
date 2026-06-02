#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils import write_text
from projects.core.dsl.utils.k8s import (
    apply_manifest,
    condition_status,
    oc,
    oc_get_json,
)
from projects.kserve.toolbox.deploy_llmisvc.utils import render_inference_service_from_parts


@entrypoint
def run(
    *,
    config_dir: str,
    namespace: str,
    inference_service: dict,
    gateway: dict,
    model_key: str,
    model: dict,
    scheduler_profile_key: str,
    scheduler_profile: dict | None,
    model_cache: dict,
) -> str:
    """
    Deploy the llm_d LLMInferenceService and wait for its endpoint.

    Args:
        config_dir: Configuration directory
        namespace: Namespace used by llm_d
        inference_service: Inference-service configuration block
        gateway: Gateway configuration block
        model_key: Selected model key
        model: Selected model configuration
        scheduler_profile_key: Scheduler profile key
        scheduler_profile: Scheduler profile configuration
        model_cache: Model-cache configuration
    """

    context = execute_tasks(locals())
    return context.endpoint_url


@task
def delete_existing_service(args, ctx):
    """Delete existing LLMInferenceService"""

    name = args.inference_service["name"]
    oc(
        "delete",
        "llminferenceservice",
        name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )
    ctx.service_name = name
    ctx.selector = f"app.kubernetes.io/name={name}"
    return f"Deleted existing LLMInferenceService {name}"


@retry(attempts=60, delay=10, backoff=1.0)
@task
def wait_old_pods_gone(args, ctx):
    """Wait for old llm-d pods to disappear"""

    pods = oc_get_json(
        "pods", namespace=args.namespace, selector=ctx.selector, ignore_not_found=True
    )
    if not pods or not pods.get("items"):
        return f"Old pods gone for {ctx.service_name}"
    return False  # Retry


@task
def apply_inference_service(args, ctx):
    """Apply the LLMInferenceService manifest"""

    manifest = render_inference_service_from_parts(
        config_dir=args.config_dir,
        namespace=args.namespace,
        inference_service=args.inference_service,
        model_key=args.model_key,
        model=args.model,
        scheduler_profile_key=args.scheduler_profile_key,
        scheduler_profile=args.scheduler_profile,
        model_cache=args.model_cache,
    )
    apply_manifest(args.artifact_dir / "src" / "llminferenceservice.yaml", manifest)
    return f"Applied LLMInferenceService manifest for {ctx.service_name}"


@retry(attempts=120, delay=5, backoff=1.0)
@task
def wait_pods_appear(args, ctx):
    """Wait for llm-d pods to appear"""

    pods = oc_get_json(
        "pods", namespace=args.namespace, selector=ctx.selector, ignore_not_found=True
    )
    if pods and pods.get("items"):
        return f"Pods appeared for {ctx.service_name}"
    return False  # Retry


@retry(attempts=90, delay=10, backoff=1.0)
@task
def wait_service_ready(args, ctx):
    """Wait for LLMInferenceService to be ready"""

    payload = oc_get_json("llminferenceservice", name=ctx.service_name, namespace=args.namespace)
    if condition_status(payload, "Ready") == "True":
        return f"LLMInferenceService {ctx.service_name} ready"
    return False  # Retry


@retry(attempts=90, delay=10, backoff=1.0)
@task
def resolve_endpoint_task(args, ctx):
    """Resolve the gateway endpoint URL"""

    endpoint_url = try_resolve_endpoint_url(
        namespace=args.namespace,
        inference_service=args.inference_service,
        gateway=args.gateway,
    )
    if endpoint_url:
        ctx.endpoint_url = endpoint_url
        write_text(args.artifact_dir / "artifacts" / "endpoint.url", f"{endpoint_url}\n")
        return f"Endpoint resolved: {endpoint_url}"
    return False  # Retry


@task
def deploy_llmisvc_task(args, ctx):
    """Deploy the llm_d inference service and resolve its endpoint"""

    # All work is done by the individual tasks
    return f"LLMInferenceService deployment completed: {ctx.endpoint_url}"


def try_resolve_endpoint_url(
    *, namespace: str, inference_service: dict, gateway: dict
) -> str | None:
    name = inference_service["name"]
    gateway_name = gateway["status_address_name"]
    payload = oc_get_json("llminferenceservice", name=name, namespace=namespace)

    for address in payload.get("status", {}).get("addresses", []):
        if address.get("name") == gateway_name and address.get("url"):
            return address["url"]
    return None


if __name__ == "__main__":
    run.main()
