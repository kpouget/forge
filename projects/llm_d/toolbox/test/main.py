#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from projects.core.dsl import toolbox
from projects.llm_d.orchestration import llmd_runtime

LOGGER = logging.getLogger(__name__)


def run() -> int:
    llmd_runtime.init()
    config = llmd_runtime.load_run_configuration()
    return run_test(config)


def run_test(config: llmd_runtime.ResolvedConfig) -> int:
    name = config.platform["inference_service"]["name"]
    namespace = config.namespace
    artifacts_dir = config.artifact_dir / "artifacts"

    LOGGER.info("Testing llm_d preset=%s namespace=%s", config.preset_name, namespace)

    endpoint_url = None
    try:
        endpoint_url = deploy_inference_service(config)
        smoke_response = run_smoke_request(config, endpoint_url)
        llmd_runtime.write_json(artifacts_dir / "smoke.response.json", smoke_response)

        if config.benchmark:
            run_guidellm_benchmark(config, endpoint_url)

        return 0
    finally:
        capture_inference_service_state(config)
        if endpoint_url:
            llmd_runtime.write_text(artifacts_dir / "endpoint.url", f"{endpoint_url}\n")
        benchmark_name = (
            config.benchmark["job_name"] if config.benchmark else "guidellm-benchmark"
        )
        llmd_runtime.oc(
            "delete",
            "job,pvc",
            benchmark_name,
            "-n",
            namespace,
            "--ignore-not-found=true",
            check=False,
        )
        llmd_runtime.oc(
            "delete",
            "pod",
            f"{benchmark_name}-copy",
            "-n",
            namespace,
            "--ignore-not-found=true",
            check=False,
        )
        events = llmd_runtime.oc(
            "get",
            "events",
            "-n",
            namespace,
            "--sort-by=.metadata.creationTimestamp",
            check=False,
            capture_output=True,
        )
        if events.returncode == 0 and events.stdout:
            llmd_runtime.write_text(
                artifacts_dir / "namespace.events.txt", events.stdout
            )


def deploy_inference_service(config: llmd_runtime.ResolvedConfig) -> str:
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
    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "llminferenceservice.yaml", manifest
    )

    def _pods_present() -> bool:
        pods = llmd_runtime.oc_get_json(
            "pods", namespace=namespace, selector=selector, ignore_not_found=True
        )
        return bool(pods and pods.get("items"))

    llmd_runtime.wait_until(
        f"llm-d pods to appear in {namespace}",
        timeout_seconds=config.platform["inference_service"][
            "pod_appearance_timeout_seconds"
        ],
        interval_seconds=5,
        predicate=_pods_present,
    )

    def _service_ready() -> bool:
        payload = llmd_runtime.oc_get_json(
            "llminferenceservice", name=name, namespace=namespace
        )
        return llmd_runtime.condition_status(payload, "Ready") == "True"

    llmd_runtime.wait_until(
        f"llminferenceservice/{name} ready",
        timeout_seconds=config.platform["inference_service"]["ready_timeout_seconds"],
        interval_seconds=10,
        predicate=_service_ready,
    )

    return llmd_runtime.wait_until(
        f"gateway address for llminferenceservice/{name}",
        timeout_seconds=config.platform["inference_service"]["ready_timeout_seconds"],
        interval_seconds=10,
        predicate=lambda: try_resolve_endpoint_url(config),
    )


def resolve_endpoint_url(config: llmd_runtime.ResolvedConfig) -> str:
    endpoint_url = try_resolve_endpoint_url(config)
    if endpoint_url:
        return endpoint_url

    name = config.platform["inference_service"]["name"]
    gateway_name = config.platform["gateway"]["status_address_name"]
    raise RuntimeError(
        f"Gateway address {gateway_name} is missing from llminferenceservice/{name} status.addresses"
    )


def try_resolve_endpoint_url(config: llmd_runtime.ResolvedConfig) -> str | None:
    name = config.platform["inference_service"]["name"]
    namespace = config.namespace
    gateway_name = config.platform["gateway"]["status_address_name"]
    payload = llmd_runtime.oc_get_json(
        "llminferenceservice", name=name, namespace=namespace
    )

    for address in payload.get("status", {}).get("addresses", []):
        if address.get("name") == gateway_name and address.get("url"):
            return address["url"]
    return None


def run_smoke_request(
    config: llmd_runtime.ResolvedConfig, endpoint_url: str
) -> dict[str, object]:
    namespace = config.namespace
    name = config.platform["inference_service"]["name"]
    deployment_name = f"{name}{config.platform['inference_service']['workload_deployment_name_suffix']}"

    payload = {
        "model": config.model["served_model_name"],
        "prompt": config.smoke_request["prompt"],
        "max_tokens": config.smoke_request["max_tokens"],
        "temperature": config.smoke_request["temperature"],
    }
    llmd_runtime.write_json(
        config.artifact_dir / "artifacts" / "smoke.request.json", payload
    )

    retries = config.platform["smoke"]["request_retries"]
    delay = config.platform["smoke"]["request_retry_delay_seconds"]
    result = None
    for _ in range(retries):
        result = llmd_runtime.oc(
            "exec",
            "-n",
            namespace,
            f"deployment/{deployment_name}",
            "-c",
            "main",
            "--",
            "curl",
            "-k",
            "-sSf",
            f"{endpoint_url}{config.platform['smoke']['endpoint_path']}",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            break
        time.sleep(delay)

    if result is None or result.returncode != 0:
        raise RuntimeError("Smoke request never succeeded against the llm_d endpoint")

    response = json.loads(result.stdout)
    if not response.get("choices"):
        raise RuntimeError(f"Invalid smoke response payload: {result.stdout}")
    return response


def run_guidellm_benchmark(
    config: llmd_runtime.ResolvedConfig, endpoint_url: str
) -> None:
    benchmark_name = config.benchmark["job_name"]
    namespace = config.namespace

    llmd_runtime.oc(
        "delete",
        "job,pvc",
        benchmark_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )
    llmd_runtime.oc(
        "delete",
        "pod",
        f"{benchmark_name}-copy",
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )

    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "guidellm-pvc.yaml",
        llmd_runtime.render_guidellm_pvc(config),
    )
    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "guidellm-job.yaml",
        llmd_runtime.render_guidellm_job(config, endpoint_url),
    )

    def _job_terminal() -> dict[str, object] | None:
        payload = llmd_runtime.oc_get_json(
            "job", name=benchmark_name, namespace=namespace
        )
        status = payload.get("status", {})
        if status.get("succeeded"):
            return payload
        if status.get("failed"):
            raise RuntimeError(f"GuideLLM job {benchmark_name} failed")
        return None

    llmd_runtime.wait_until(
        f"GuideLLM job/{benchmark_name}",
        timeout_seconds=config.benchmark["timeout_seconds"],
        interval_seconds=10,
        predicate=_job_terminal,
    )

    capture_guidellm_state(config)
    copy_guidellm_results(config)


def copy_guidellm_results(config: llmd_runtime.ResolvedConfig) -> None:
    benchmark_name = config.benchmark["job_name"]
    namespace = config.namespace
    pod_data = llmd_runtime.oc_get_json(
        "pods",
        namespace=namespace,
        selector=f"job-name={benchmark_name}",
        ignore_not_found=True,
    )
    node_name = None
    if pod_data and pod_data.get("items"):
        node_name = pod_data["items"][0].get("spec", {}).get("nodeName")

    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "guidellm-copy-pod.yaml",
        llmd_runtime.render_guidellm_copy_pod(config, node_name=node_name),
    )

    def _helper_ready() -> bool:
        payload = llmd_runtime.oc_get_json(
            "pod",
            name=f"{benchmark_name}-copy",
            namespace=namespace,
        )
        conditions = payload.get("status", {}).get("conditions", [])
        return any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in conditions
        )

    llmd_runtime.wait_until(
        f"GuideLLM copy helper pod/{benchmark_name}-copy",
        timeout_seconds=120,
        interval_seconds=5,
        predicate=_helper_ready,
    )

    result = llmd_runtime.oc(
        "exec",
        "-n",
        namespace,
        f"{benchmark_name}-copy",
        "--",
        "cat",
        "/results/benchmarks.json",
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        llmd_runtime.write_text(
            config.artifact_dir / "artifacts" / "results" / "benchmarks.json",
            result.stdout,
        )


def capture_inference_service_state(config: llmd_runtime.ResolvedConfig) -> None:
    name = config.platform["inference_service"]["name"]
    namespace = config.namespace
    artifacts_dir = config.artifact_dir / "artifacts"
    selector = f"app.kubernetes.io/name={name}"

    capture_get(
        "llminferenceservice",
        name,
        namespace,
        "yaml",
        artifacts_dir / "llminferenceservice.yaml",
    )
    capture_get(
        "llminferenceservice",
        name,
        namespace,
        "json",
        artifacts_dir / "llminferenceservice.json",
    )
    capture_get(
        "pods",
        None,
        namespace,
        "yaml",
        artifacts_dir / "llminferenceservice.pods.yaml",
        selector=selector,
    )
    capture_get(
        "deployments",
        None,
        namespace,
        "yaml",
        artifacts_dir / "llminferenceservice.deployments.yaml",
        selector=selector,
    )
    capture_get(
        "replicasets",
        None,
        namespace,
        "yaml",
        artifacts_dir / "llminferenceservice.replicasets.yaml",
        selector=selector,
    )
    capture_get(
        "pods", None, namespace, "wide", artifacts_dir / "namespace.pods.status"
    )
    capture_get(
        "services", None, namespace, "wide", artifacts_dir / "namespace.services.status"
    )

    pod_list = llmd_runtime.oc_get_json(
        "pods", namespace=namespace, selector=selector, ignore_not_found=True
    )
    if pod_list:
        lines = []
        previous_lines = []
        for pod in pod_list.get("items", []):
            pod_name = pod["metadata"]["name"]
            lines.append(f"=== {pod_name} ===")
            log_result = llmd_runtime.oc(
                "logs",
                pod_name,
                "-n",
                namespace,
                "--all-containers=true",
                check=False,
                capture_output=True,
            )
            if log_result.stdout:
                lines.append(log_result.stdout.rstrip())

            previous_lines.append(f"=== {pod_name} ===")
            previous_result = llmd_runtime.oc(
                "logs",
                pod_name,
                "-n",
                namespace,
                "--previous",
                "--all-containers=true",
                check=False,
                capture_output=True,
            )
            if previous_result.stdout:
                previous_lines.append(previous_result.stdout.rstrip())

        llmd_runtime.write_text(
            artifacts_dir / "llminferenceservice.pods.logs", "\n".join(lines) + "\n"
        )
        llmd_runtime.write_text(
            artifacts_dir / "llminferenceservice.pods.previous.logs",
            "\n".join(previous_lines) + "\n",
        )


def capture_guidellm_state(config: llmd_runtime.ResolvedConfig) -> None:
    benchmark_name = config.benchmark["job_name"]
    namespace = config.namespace
    artifacts_dir = config.artifact_dir / "artifacts"

    capture_get(
        "job",
        benchmark_name,
        namespace,
        "yaml",
        artifacts_dir / "guidellm_benchmark_job.yaml",
    )
    capture_get(
        "pods",
        None,
        namespace,
        "yaml",
        artifacts_dir / "guidellm_benchmark_job.pods.yaml",
        selector=f"job-name={benchmark_name}",
    )
    result = llmd_runtime.oc(
        "logs",
        f"job/{benchmark_name}",
        "-n",
        namespace,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        llmd_runtime.write_text(
            artifacts_dir / "guidellm_benchmark_job.logs", result.stdout
        )


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
