from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from projects.core.dsl import shell
from projects.llm_d.runtime import llmd_runtime
from projects.llm_d.toolbox.capture_llmisvc_state import main as capture_llmisvc_state
from projects.llm_d.toolbox.deploy_llmisvc import main as deploy_llmisvc
from projects.llm_d.toolbox.run_guidellm_benchmark import main as run_guidellm_benchmark_command
from projects.llm_d.toolbox.run_smoke_request import main as run_smoke_request_command

LOGGER = logging.getLogger(__name__)


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
    smoke: dict,
    smoke_request: dict,
    benchmark: dict | None = None,
    capture_namespace_events: bool = True,
) -> int:
    artifact_dir = llmd_runtime.init()

    endpoint_url: str | None = None
    primary_exc: tuple[type[BaseException], BaseException, Any] | None = None
    finalizer_exc: tuple[type[BaseException], BaseException, Any] | None = None

    try:
        endpoint_url = deploy_inference_service(
            config_dir=config_dir,
            namespace=namespace,
            inference_service=inference_service,
            gateway=gateway,
            model_key=model_key,
            model=model,
            scheduler_profile_key=scheduler_profile_key,
            scheduler_profile=scheduler_profile,
            model_cache=model_cache,
        )
        run_smoke_request(
            namespace=namespace,
            smoke=smoke,
            model=model,
            smoke_request=smoke_request,
            endpoint_url=endpoint_url,
        )
        run_guidellm_benchmark(
            namespace=namespace,
            benchmark=benchmark,
            endpoint_url=endpoint_url,
        )
    except Exception:
        primary_exc = sys.exc_info()
    finally:
        finalizer_exc = _run_finalizer(
            primary_exc,
            finalizer_exc,
            "capture inference-service state",
            capture_inference_service_state,
            namespace=namespace,
            inference_service=inference_service,
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
            "cleanup runtime resources",
            cleanup_runtime_resources,
            namespace=namespace,
            inference_service=inference_service,
            smoke=smoke,
            benchmark=benchmark,
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


def deploy_inference_service(
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
    return deploy_llmisvc.run(
        config_dir=config_dir,
        namespace=namespace,
        inference_service=inference_service,
        gateway=gateway,
        model_key=model_key,
        model=model,
        scheduler_profile_key=scheduler_profile_key,
        scheduler_profile=scheduler_profile,
        model_cache=model_cache,
    )


def run_smoke_request(
    *,
    namespace: str,
    smoke: dict,
    model: dict,
    smoke_request: dict,
    endpoint_url: str,
) -> dict[str, object]:
    return run_smoke_request_command.run(
        namespace=namespace,
        smoke=smoke,
        model=model,
        smoke_request=smoke_request,
        endpoint_url=endpoint_url,
    )


def run_guidellm_benchmark(*, namespace: str, benchmark: dict | None, endpoint_url: str) -> None:
    if not benchmark:
        return

    run_guidellm_benchmark_command.run(
        namespace=namespace,
        benchmark=benchmark,
        endpoint_url=endpoint_url,
    )


def capture_inference_service_state(*, namespace: str, inference_service: dict) -> None:
    capture_llmisvc_state.run(
        llmisvc_name=inference_service["name"],
        namespace=namespace,
    )


def write_endpoint_url(*, artifact_dir: Path, endpoint_url: str | None) -> None:
    if not endpoint_url:
        return

    llmd_runtime.write_text(artifact_dir / "artifacts" / "endpoint.url", f"{endpoint_url}\n")


def cleanup_runtime_resources(
    *,
    namespace: str,
    inference_service: dict,
    smoke: dict,
    benchmark: dict | None,
) -> None:
    benchmark_name = benchmark["job_name"] if benchmark else "guidellm-benchmark"
    smoke_job_name = smoke["job_name"]
    inference_service_name = inference_service["name"]
    cleanup_timeout_seconds = inference_service["delete_timeout_seconds"]

    _best_effort_delete(
        "smoke helper job",
        "delete",
        "job",
        smoke_job_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "benchmark helper job and pvc",
        "delete",
        "job,pvc",
        benchmark_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "benchmark helper copy pod",
        "delete",
        "pod",
        f"{benchmark_name}-copy",
        "-n",
        namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "llminferenceservice",
        "delete",
        "llminferenceservice",
        inference_service_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
    )

    llmd_runtime.wait_until(
        f"llminferenceservice/{inference_service_name} deletion in {namespace}",
        timeout_seconds=cleanup_timeout_seconds,
        interval_seconds=10,
        predicate=lambda: (
            not llmd_runtime.resource_exists(
                "llminferenceservice", inference_service_name, namespace=namespace
            )
        ),
    )
    llmd_runtime.wait_until(
        f"llm-d workload pods deletion in {namespace}",
        timeout_seconds=cleanup_timeout_seconds,
        interval_seconds=10,
        predicate=lambda: _llm_d_pods_gone(namespace, inference_service_name),
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
            LOGGER.exception("Finalizer failed: %s", description)
            return finalizer_exc or sys.exc_info()
        LOGGER.exception("Ignoring %s failure after primary test failure", description)
    return finalizer_exc


def _best_effort_delete(description: str, *oc_args: str) -> None:
    try:
        llmd_runtime.oc(*oc_args, check=False, timeout_seconds=60)
    except subprocess.TimeoutExpired:
        LOGGER.warning("Timed out deleting %s: oc %s", description, " ".join(oc_args))


def _llm_d_pods_gone(namespace: str, inference_service_name: str) -> bool:
    payload = llmd_runtime.oc_get_json(
        "pods",
        namespace=namespace,
        selector=f"app.kubernetes.io/name={inference_service_name}",
        ignore_not_found=True,
    )
    return not payload or not payload.get("items")
