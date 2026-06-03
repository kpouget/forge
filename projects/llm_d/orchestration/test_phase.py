from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Any

from projects.core.dsl import shell
from projects.core.dsl.utils import slugify_identifier, truncate_k8s_name
from projects.core.dsl.utils.k8s import oc_get_json, oc_resource_exists
from projects.core.library import env
from projects.core.library.run import SignalInterrupt
from projects.core.orchestration.utils.k8s import ensure_namespace
from projects.guidellm.toolbox.run_guidellm_benchmark import main as run_guidellm_benchmark_command
from projects.guidellm.toolbox.run_smoke_request import main as run_smoke_request_command
from projects.kserve.toolbox.capture_llmisvc_state import main as capture_llmisvc_state
from projects.kserve.toolbox.deploy_llmisvc import main as deploy_llmisvc
from projects.kserve.toolbox.deploy_llmisvc.utils import render_inference_service_from_parts
from projects.kserve.toolbox.prepare_hf_model_cache.main import (
    run as prepare_hf_model_cache_toolbox_run,
)
from projects.llm_d.orchestration.utils import write_yaml
from projects.llm_d.toolbox.cleanup_test_resources import main as cleanup_test_resources_command

logger = logging.getLogger(__name__)


def compute_expected_pvc_name(model_key: str, model_uri: str, pvc_name_prefix: str) -> str:
    """Compute the expected PVC name using the same logic as the prepare phase."""
    cache_key = hashlib.sha256(model_uri.encode("utf-8")).hexdigest()[:10]
    return truncate_k8s_name(
        f"{pvc_name_prefix}-{slugify_identifier(model_key, max_length=32)}-{cache_key}"
    )


def ensure_model_cache_pvc(
    *,
    namespace: str,
    model_key: str,
    model: dict,
    model_cache: dict,
) -> None:
    """Validate that model cache PVC exists, create it if missing."""
    if not model_cache.get("enabled", False):
        logger.info("Model cache disabled, skipping PVC validation")
        return

    model_uri = model["uri"]

    # Skip caching for PVC-based models
    if model_uri.startswith(("pvc://", "pvc+hf://")):
        logger.info("Skipping cache validation for PVC-based model: %s", model_uri)
        return

    # Only handle HF models for now
    if not model_uri.startswith("hf://"):
        logger.info("Skipping cache validation for non-HF model: %s", model_uri)
        return

    # Compute expected PVC name
    model_cache_overrides = model.get("cache", {})
    pvc_name_prefix = model_cache["pvc"]["name_prefix"]
    expected_pvc_name = compute_expected_pvc_name(model_key, model_uri, pvc_name_prefix)

    # Step 1: Check if PVC exists
    if not oc_resource_exists("persistentvolumeclaim", expected_pvc_name, namespace=namespace):
        logger.warning("Model cache PVC %s not found, preparing it now", expected_pvc_name)
    else:
        logger.info("Model cache PVC %s exists", expected_pvc_name)

        # Step 2: Check if PVC has the populated label
        pvc_data = oc_get_json(
            "persistentvolumeclaim",
            name=expected_pvc_name,
            namespace=namespace,
            ignore_not_found=True,
        )

        if pvc_data:
            labels = pvc_data.get("metadata", {}).get("labels", {})
            is_populated = labels.get("forge.openshift.io/model-cache-populated") == "true"

            if is_populated:
                logger.info(
                    "Model cache PVC %s is labeled as populated, ready to use", expected_pvc_name
                )
                return
            else:
                logger.warning(
                    "Model cache PVC %s exists but is missing 'populated' label, will repopulate",
                    expected_pvc_name,
                )
        else:
            logger.warning(
                "Model cache PVC %s exists but cannot read metadata, will repopulate",
                expected_pvc_name,
            )

    # Prepare the model cache
    prepare_hf_model_cache_toolbox_run(
        namespace=namespace,
        namespace_is_managed=True,  # Assume managed during testing
        model_key=model_key,
        model_uri=model_uri,
        pvc_size=model_cache_overrides.get("pvc_size", model_cache["pvc"]["size"]),
        access_mode=model_cache_overrides.get("access_mode", model_cache["pvc"]["access_mode"]),
        storage_class_name=model_cache_overrides.get(
            "storage_class_name", model_cache["pvc"].get("storage_class_name")
        ),
        pvc_name_prefix=pvc_name_prefix,
        model_directory_name=model_cache["pvc"]["model_directory_name"],
        downloader_image=model_cache["hf"]["downloader_image"],
        hf_token_file_path=None,  # TODO: Could get from vault if needed
    )


def run() -> int:
    artifact_dir = env.ARTIFACT_DIR

    # Load minimal config needed for orchestration flow
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    capture_namespace_events = platform["artifacts"]["capture_namespace_events"]

    # Ensure namespace exists before starting any deployments
    ensure_namespace(
        namespace,
        labels={
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        },
    )

    endpoint_url: str | None = None
    primary_exc: tuple[type[BaseException], BaseException, Any] | None = None
    finalizer_exc: tuple[type[BaseException], BaseException, Any] | None = None

    try:
        endpoint_url = deploy_inference_service()

        if not endpoint_url:
            raise ValueError("Failed to extract the endpoint_url from the LLMISVC deployment")
        run_smoke_request(endpoint_url=endpoint_url)

        run_guidellm_benchmark(endpoint_url=endpoint_url)
    except Exception:
        primary_exc = sys.exc_info()
    except SignalInterrupt:
        primary_exc = sys.exc_info()
    finally:
        do_finalizers = True
        if primary_exc and isinstance(primary_exc[1], SignalInterrupt):
            logging.warning("Caught a SignalInterrupt, skipping the finalizers")
            do_finalizers = False

        # Always run cleanup, even on signal interrupt
        finalizer_exc = _run_finalizer(
            primary_exc,
            finalizer_exc,
            "cleanup runtime resources",
            cleanup_test_resources,
        )

        if do_finalizers:
            finalizer_exc = _run_finalizer(
                primary_exc,
                finalizer_exc,
                "capture inference-service state",
                capture_inference_service_state,
            )
            finalizer_exc = _run_finalizer(
                primary_exc,
                finalizer_exc,
                "write endpoint URL",
                write_endpoint_url,
                artifact_dir=artifact_dir,
                endpoint_url=endpoint_url,
            )
            finalizer_exc = _run_finalizer(
                primary_exc,
                finalizer_exc,
                "capture namespace events",
                capture_namespace_events_after_test,
                artifact_dir=artifact_dir,
                namespace=namespace,
                capture_namespace_events=capture_namespace_events,
            )

    if primary_exc is not None:
        raise primary_exc[1].with_traceback(primary_exc[2])
    if finalizer_exc is not None:
        raise finalizer_exc[1].with_traceback(finalizer_exc[2])

    return 0


def deploy_inference_service() -> str:
    """Deploy LLMInferenceService and return endpoint URL.

    Returns:
        Gateway endpoint URL for the deployed service
    """
    logger.info("Starting LLMInferenceService deployment")

    # Load config where it's consumed
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    gateway = platform["gateway"]

    # Step 1: Ensure model cache is ready
    _prepare_model_cache()

    # Step 2: Build and write inference service manifest
    manifest_path = _build_inference_service_manifest()

    # Step 3: Deploy the service and wait for endpoint
    logger.info("Deploying LLMInferenceService from manifest: %s", manifest_path)
    endpoint_url = deploy_llmisvc.run(
        namespace=namespace,
        inference_service_manifest_path=str(manifest_path),
        gateway_status_address_name=gateway["status_address_name"],
    )

    logger.info("LLMInferenceService deployed successfully, endpoint: %s", endpoint_url)
    return endpoint_url


def _prepare_model_cache() -> None:
    """Ensure model cache PVC is ready for deployment."""
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    model_key = runtime_config.get_model_key()
    model = runtime_config.get_model()
    model_cache = runtime_config.get_model_cache_config()

    logger.info("Validating model cache for model: %s", model_key)
    ensure_model_cache_pvc(
        namespace=namespace,
        model_key=model_key,
        model=model,
        model_cache=model_cache,
    )


def _build_inference_service_manifest() -> Path:
    """Build and write the LLMInferenceService manifest."""
    from projects.llm_d.orchestration import runtime_config

    config_dir = runtime_config.get_config_dir()
    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    inference_service = platform["inference_service"]
    model_key = runtime_config.get_model_key()
    model = runtime_config.get_model()
    scheduler_profile_key = runtime_config.get_scheduler_profile_key()
    scheduler_profile = runtime_config.get_scheduler_profile()
    model_cache = runtime_config.get_model_cache_config()

    # Convert from old key+dict format to direct path format
    scheduler_profile_config_path = None
    if scheduler_profile_key != "default" and scheduler_profile is not None:
        scheduler_profile_config_path = scheduler_profile["config_path"]

    # Build the InferenceService manifest
    manifest = render_inference_service_from_parts(
        config_dir=config_dir,
        namespace=namespace,
        inference_service=inference_service,
        model_key=model_key,
        model=model,
        scheduler_profile_config_path=scheduler_profile_config_path,
        model_cache=model_cache,
    )

    # Write the manifest to artifacts
    artifacts_dir = env.ARTIFACT_DIR / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifacts_dir / "llminferenceservice.yaml"
    write_yaml(manifest_path, manifest)

    logger.info("Built LLMInferenceService manifest: %s", manifest_path)
    return manifest_path


def run_smoke_request(*, endpoint_url: str) -> dict[str, object]:
    # Load config where it's consumed
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    smoke = platform["smoke"]
    model = runtime_config.get_model()
    smoke_request = runtime_config.get_smoke_request()

    return run_smoke_request_command.run(
        namespace=namespace,
        endpoint_url=endpoint_url,
        pod_name=smoke["pod_name"],
        client_image=smoke["client_image"],
        endpoint_path=smoke["endpoint_path"],
        request_timeout_seconds=smoke["request_timeout_seconds"],
        served_model_name=model["served_model_name"],
        prompt=smoke_request["prompt"],
        max_tokens=smoke_request["max_tokens"],
        temperature=smoke_request["temperature"],
    )


def run_guidellm_benchmark(*, endpoint_url: str) -> None:
    # Load config where it's consumed
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    benchmark = runtime_config.get_benchmark_config()

    if not benchmark:
        return  # Skip if benchmark is disabled

    # Build guidellm args from benchmark.args dictionary
    guidellm_args = []
    if "args" in benchmark:
        for key, value in benchmark["args"].items():
            # Convert snake_case to kebab-case for CLI args
            cli_key = key.replace("_", "-")
            guidellm_args.append(f"--{cli_key}={value}")

    # Add rate if specified at top level (and not already in args)
    if "rate" in benchmark and "rate" not in benchmark.get("args", {}):
        guidellm_args.append(f"--rate={benchmark['rate']}")

    # Add outputs if not in args
    if not any(arg.startswith("--outputs=") for arg in guidellm_args):
        guidellm_args.append(f"--outputs={benchmark.get('outputs', 'json')}")

    run_guidellm_benchmark_command.run(
        endpoint_url=endpoint_url,
        name=benchmark.get("job_name"),
        namespace=namespace,
        image=benchmark.get("image"),
        version=benchmark.get("version"),
        timeout=benchmark.get("timeout_seconds"),
        pvc_size=benchmark.get("pvc_size"),
        guidellm_args=guidellm_args,
    )


def capture_inference_service_state() -> None:
    # Load config where it's consumed
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    inference_service = platform["inference_service"]

    capture_llmisvc_state.run(
        llmisvc_name=inference_service["name"],
        namespace=namespace,
    )


def write_endpoint_url(*, artifact_dir: Path, endpoint_url: str | None) -> None:
    if not endpoint_url:
        return

    endpoint_file = artifact_dir / "artifacts" / "endpoint.url"
    endpoint_file.parent.mkdir(parents=True, exist_ok=True)
    endpoint_file.write_text(f"{endpoint_url}\n", encoding="utf-8")


def cleanup_test_resources() -> None:
    """Cleanup test resources using the toolbox script"""
    # Load config where it's consumed
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    platform = runtime_config.get_platform_config()
    inference_service = platform["inference_service"]
    smoke = platform["smoke"]
    benchmark = runtime_config.get_benchmark_config()

    benchmark_job_name = benchmark["job_name"] if benchmark else None

    cleanup_test_resources_command.run(
        namespace=namespace,
        inference_service_name=inference_service["name"],
        smoke_pod_name=smoke["pod_name"],
        benchmark_job_name=benchmark_job_name,
    )


def capture_namespace_events_after_test(
    *,
    artifact_dir: Path,
    namespace: str,
    capture_namespace_events: bool,
) -> None:
    if not capture_namespace_events:
        return

    shell.run(
        f"oc get events -n {namespace} --sort-by=.metadata.creationTimestamp",
        check=False,
        stdout_dest=artifact_dir / "artifacts" / "namespace.events.txt",
    )


def _run_finalizer(
    primary_exc: tuple[type[BaseException], BaseException, Any] | None,
    finalizer_exc: tuple[type[BaseException], BaseException, Any] | None,
    description: str,
    callback,
    **kwargs,
) -> tuple[type[BaseException], BaseException, Any] | None:
    try:
        callback(**kwargs)
    except Exception:
        if primary_exc is None:
            logger.exception("Finalizer failed: %s", description)
            return finalizer_exc or sys.exc_info()
        logger.exception("Ignoring %s failure after primary test failure", description)
    return finalizer_exc
