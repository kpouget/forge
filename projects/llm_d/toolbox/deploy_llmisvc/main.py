#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime, phase_inputs


def run(
    *,
    config_dir: str,
    namespace: str,
    inference_service: dict,
    gateway: dict,
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
        model: Selected model configuration
        scheduler_profile_key: Scheduler profile key
        scheduler_profile: Scheduler profile configuration
        model_cache: Model-cache configuration
    """

    llmd_runtime.init()
    context = execute_tasks(locals())
    return context.endpoint_url


@task
def deploy_llmisvc_task(args, ctx):
    """Deploy the llm_d inference service and resolve its endpoint"""

    config = phase_inputs.build_test_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=args.config_dir,
        preset_name="deploy-llmisvc",
        namespace=args.namespace,
        platform={
            "inference_service": args.inference_service,
            "gateway": args.gateway,
        },
        model_key="unused",
        model=args.model,
        scheduler_profile_key=args.scheduler_profile_key,
        scheduler_profile=args.scheduler_profile,
        model_cache=args.model_cache,
        smoke_request={},
        benchmark=None,
    )
    ctx.endpoint_url = deploy_llmisvc(config)
    return f"Endpoint resolved: {ctx.endpoint_url}"


def deploy_llmisvc(config: phase_inputs.TestInputs) -> str:
    name = config.platform["inference_service"]["name"]
    namespace = config.namespace
    selector = f"app.kubernetes.io/name={name}"

    llmd_runtime.oc(
        "delete",
        "llminferenceservice",
        name,
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )

    def _old_pods_gone() -> bool:
        pods = llmd_runtime.oc_get_json(
            "pods", namespace=namespace, selector=selector, ignore_not_found=True
        )
        return not pods or not pods.get("items")

    llmd_runtime.wait_until(
        f"old llm-d pods to disappear in {namespace}",
        timeout_seconds=config.platform["inference_service"]["delete_timeout_seconds"],
        interval_seconds=10,
        predicate=_old_pods_gone,
    )

    manifest = llmd_runtime.render_inference_service(config)
    llmd_runtime.apply_manifest(config.artifact_dir / "src" / "llminferenceservice.yaml", manifest)

    def _pods_present() -> bool:
        pods = llmd_runtime.oc_get_json(
            "pods", namespace=namespace, selector=selector, ignore_not_found=True
        )
        return bool(pods and pods.get("items"))

    llmd_runtime.wait_until(
        f"llm-d pods to appear in {namespace}",
        timeout_seconds=config.platform["inference_service"]["pod_appearance_timeout_seconds"],
        interval_seconds=5,
        predicate=_pods_present,
    )

    def _service_ready() -> bool:
        payload = llmd_runtime.oc_get_json("llminferenceservice", name=name, namespace=namespace)
        return llmd_runtime.condition_status(payload, "Ready") == "True"

    llmd_runtime.wait_until(
        f"llminferenceservice/{name} ready",
        timeout_seconds=config.platform["inference_service"]["ready_timeout_seconds"],
        interval_seconds=10,
        predicate=_service_ready,
    )

    endpoint_url = llmd_runtime.wait_until(
        f"gateway address for llminferenceservice/{name}",
        timeout_seconds=config.platform["inference_service"]["ready_timeout_seconds"],
        interval_seconds=10,
        predicate=lambda: try_resolve_endpoint_url(config),
    )
    llmd_runtime.write_text(config.artifact_dir / "artifacts" / "endpoint.url", f"{endpoint_url}\n")
    return endpoint_url


def resolve_endpoint_url(config: phase_inputs.TestInputs) -> str:
    endpoint_url = try_resolve_endpoint_url(config)
    if endpoint_url:
        return endpoint_url

    name = config.platform["inference_service"]["name"]
    gateway_name = config.platform["gateway"]["status_address_name"]
    raise RuntimeError(
        f"Gateway address {gateway_name} is missing from llminferenceservice/{name} status.addresses"
    )


def try_resolve_endpoint_url(config: phase_inputs.TestInputs) -> str | None:
    name = config.platform["inference_service"]["name"]
    namespace = config.namespace
    gateway_name = config.platform["gateway"]["status_address_name"]
    payload = llmd_runtime.oc_get_json("llminferenceservice", name=name, namespace=namespace)

    for address in payload.get("status", {}).get("addresses", []):
        if address.get("name") == gateway_name and address.get("url"):
            return address["url"]
    return None


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
