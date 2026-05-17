from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from projects.llm_d.runtime import llmd_runtime, phase_inputs


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


def run_guidellm_benchmark(config: phase_inputs.TestInputs, endpoint_url: str) -> None:
    if not config.benchmark:
        return

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
        payload = llmd_runtime.oc_get_json("job", name=benchmark_name, namespace=namespace)
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


def copy_guidellm_results(config: phase_inputs.TestInputs) -> None:
    if not config.benchmark:
        return

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


def capture_guidellm_state(config: phase_inputs.TestInputs) -> None:
    if not config.benchmark:
        return

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
        llmd_runtime.write_text(artifacts_dir / "guidellm_benchmark_job.logs", result.stdout)


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
