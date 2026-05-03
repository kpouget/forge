from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
from collections.abc import Iterable
from typing import Any

import yaml

from projects.llm_d.orchestration.runtime_config import (
    CONFIG_DIR,
    ORCHESTRATION_DIR,
    ModelCacheSpec,
    ResolvedConfig,
    apply_requested_preset,
    derive_namespace,
    ensure_artifact_directories,
    init,
    load_fournos_config,
    load_run_configuration,
    load_yaml,
    normalize_gpu_count,
    parse_overrides,
    resolve_model_cache,
    slugify_identifier,
    truncate_k8s_name,
    version_tuple,
    write_json,
    write_text,
    write_yaml,
)
from projects.llm_d.orchestration.runtime_manifests import (
    load_manifest_template,
    render_datasciencecluster,
    render_gateway,
    render_guidellm_copy_pod,
    render_guidellm_job,
    render_guidellm_pvc,
    render_inference_service,
    render_model_cache_pvc,
    render_smoke_request_job,
)

LOGGER = logging.getLogger(__name__)

__all__ = [
    "CONFIG_DIR",
    "ORCHESTRATION_DIR",
    "CommandError",
    "ModelCacheSpec",
    "ResolvedConfig",
    "annotate_model_cache_pvc",
    "apply_manifest",
    "apply_requested_preset",
    "condition_status",
    "derive_namespace",
    "desired_subscription",
    "ensure_artifact_directories",
    "ensure_namespace",
    "ensure_operator_group",
    "ensure_subscription",
    "init",
    "job_pod_names",
    "load_fournos_config",
    "load_manifest_template",
    "load_run_configuration",
    "load_yaml",
    "model_cache_pvc_ready",
    "normalize_gpu_count",
    "oc",
    "oc_get_json",
    "operator_spec_by_package",
    "parse_overrides",
    "pvc_access_mode_matches",
    "render_datasciencecluster",
    "render_gateway",
    "render_guidellm_copy_pod",
    "render_guidellm_job",
    "render_guidellm_pvc",
    "render_inference_service",
    "render_model_cache_job",
    "render_model_cache_pvc",
    "render_smoke_request_job",
    "resource_exists",
    "resolve_default_serviceaccount_image_pull_secret",
    "resolve_model_cache",
    "run_command",
    "slugify_identifier",
    "subscription_spec_matches",
    "truncate_k8s_name",
    "version_tuple",
    "wait_for_crd",
    "wait_for_job_completion",
    "wait_for_namespace_deleted",
    "wait_for_operator_csv",
    "wait_for_pvc_bound",
    "wait_until",
    "write_json",
    "write_text",
    "write_yaml",
]


class CommandError(RuntimeError):
    """Raised when an external command exits unsuccessfully."""


def run_command(
    args: Iterable[str],
    *,
    check: bool = True,
    capture_output: bool = True,
    input_text: str | None = None,
    timeout_seconds: float | None = 300,
) -> subprocess.CompletedProcess[str]:
    cmd = [str(arg) for arg in args]
    LOGGER.info("run: %s", " ".join(shlex.quote(arg) for arg in cmd))
    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=capture_output,
            input=input_text,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        LOGGER.error(
            "Command timed out after %ss: %s",
            timeout_seconds,
            " ".join(shlex.quote(arg) for arg in cmd),
        )
        raise

    if capture_output:
        if result.stdout:
            LOGGER.info("stdout:\n%s", result.stdout.rstrip())
        if result.stderr:
            LOGGER.info("stderr:\n%s", result.stderr.rstrip())

    if check and result.returncode != 0:
        raise CommandError(
            f"Command failed with exit code {result.returncode}: "
            f"{' '.join(shlex.quote(arg) for arg in cmd)}"
        )

    return result


def oc(
    *args: str,
    check: bool = True,
    capture_output: bool = True,
    input_text: str | None = None,
    timeout_seconds: float | None = 300,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        ["oc", *args],
        check=check,
        capture_output=capture_output,
        input_text=input_text,
        timeout_seconds=timeout_seconds,
    )


def apply_manifest(artifact_path: Any, manifest: dict[str, Any]) -> None:
    write_yaml(artifact_path, manifest)
    oc("apply", "-f", str(artifact_path))


def oc_get_json(
    kind: str,
    *,
    name: str | None = None,
    namespace: str | None = None,
    selector: str | None = None,
    ignore_not_found: bool = False,
) -> dict[str, Any] | None:
    args = ["get", kind]
    if name:
        args.append(name)
    if namespace:
        args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    args.extend(["-o", "json"])

    result = oc(*args, check=not ignore_not_found, capture_output=True)
    if result.returncode != 0:
        if ignore_not_found and _is_oc_not_found_error(result.stderr):
            return None
        raise CommandError(
            f"oc {' '.join(shlex.quote(arg) for arg in args)} failed with exit code "
            f"{result.returncode}: {result.stderr.strip()}"
        )
    if not result.stdout:
        raise CommandError(f"oc {' '.join(shlex.quote(arg) for arg in args)} returned no output")
    return json.loads(result.stdout)


def resource_exists(kind: str, name: str, *, namespace: str | None = None) -> bool:
    return (
        oc_get_json(
            kind,
            name=name,
            namespace=namespace,
            ignore_not_found=True,
        )
        is not None
    )


def _is_oc_not_found_error(stderr: str | None) -> bool:
    if not stderr:
        return False

    normalized = stderr.lower()
    if "error from server (notfound)" in normalized:
        return True
    if "no resources found" in normalized:
        return True

    return bool(re.search(r"\bnot found\b", normalized))


def wait_until(
    description: str,
    *,
    timeout_seconds: int,
    interval_seconds: int,
    predicate,
) -> Any:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
            last_error = None
        except Exception as exc:  # pragma: no cover - exercised in integration paths
            if isinstance(exc, RuntimeError):
                raise
            last_error = exc
            LOGGER.info("waiting for %s: %s", description, exc)
        time.sleep(interval_seconds)

    if last_error:
        raise RuntimeError(f"Timed out waiting for {description}: {last_error}") from last_error
    raise RuntimeError(f"Timed out waiting for {description}")


def wait_for_namespace_deleted(namespace: str, timeout_seconds: int) -> None:
    wait_until(
        f"namespace/{namespace} deletion",
        timeout_seconds=timeout_seconds,
        interval_seconds=10,
        predicate=lambda: not resource_exists("namespace", namespace),
    )


def wait_for_crd(crd_name: str, timeout_seconds: int) -> None:
    wait_until(
        f"crd/{crd_name}",
        timeout_seconds=timeout_seconds,
        interval_seconds=10,
        predicate=lambda: resource_exists("crd", crd_name),
    )


def wait_for_operator_csv(package: str, namespace: str, timeout_seconds: int) -> dict[str, Any]:
    selector = f"operators.coreos.com/{package}.{namespace}"

    def _csv_ready() -> dict[str, Any] | None:
        data = oc_get_json("csv", namespace=namespace, selector=selector, ignore_not_found=True)
        if not data:
            return None
        items = data.get("items", [])
        if not items:
            return None
        csv = items[0]
        if csv.get("status", {}).get("phase") == "Succeeded":
            return csv
        return None

    return wait_until(
        f"{package} CSV in {namespace}",
        timeout_seconds=timeout_seconds,
        interval_seconds=15,
        predicate=_csv_ready,
    )


def ensure_namespace(namespace: str, *, labels: dict[str, str] | None = None) -> None:
    if not resource_exists("namespace", namespace):
        oc("create", "namespace", namespace)

    if labels:
        oc("label", "namespace", namespace, "--overwrite", *[f"{k}={v}" for k, v in labels.items()])


def ensure_operator_group(namespace: str, package: str) -> None:
    data = oc_get_json("operatorgroup", namespace=namespace, ignore_not_found=True)
    if data and data.get("items"):
        for item in data["items"]:
            targets = item.get("spec", {}).get("targetNamespaces") or [namespace]
            if namespace in targets:
                return
        raise RuntimeError(
            f"Existing OperatorGroup objects in {namespace} do not target {namespace}"
        )

    operator_group = {
        "apiVersion": "operators.coreos.com/v1",
        "kind": "OperatorGroup",
        "metadata": {"name": package, "namespace": namespace},
        "spec": {"targetNamespaces": [namespace]},
    }
    oc("apply", "-f", "-", input_text=yaml.safe_dump(operator_group, sort_keys=False))


def ensure_subscription(operator_spec: dict[str, Any]) -> None:
    namespace = operator_spec["namespace"]
    package = operator_spec["package"]

    ensure_namespace(namespace)
    ensure_operator_group(namespace, package)

    subscription = desired_subscription(operator_spec)
    current = oc_get_json(
        "subscription.operators.coreos.com",
        name=package,
        namespace=namespace,
        ignore_not_found=True,
    )
    if current and not subscription_spec_matches(current.get("spec", {}), subscription["spec"]):
        LOGGER.info("Reconciling subscription drift for %s in %s", package, namespace)

    oc("apply", "-f", "-", input_text=yaml.safe_dump(subscription, sort_keys=False))

    def _subscription_reconciled() -> dict[str, Any] | None:
        payload = oc_get_json(
            "subscription.operators.coreos.com",
            name=package,
            namespace=namespace,
        )
        if subscription_spec_matches(payload.get("spec", {}), subscription["spec"]):
            return payload
        return None

    wait_until(
        f"subscription/{package} reconciliation in {namespace}",
        timeout_seconds=60,
        interval_seconds=5,
        predicate=_subscription_reconciled,
    )


def desired_subscription(operator_spec: dict[str, Any]) -> dict[str, Any]:
    namespace = operator_spec["namespace"]
    package = operator_spec["package"]
    return {
        "apiVersion": "operators.coreos.com/v1alpha1",
        "kind": "Subscription",
        "metadata": {"name": package, "namespace": namespace},
        "spec": {
            "channel": operator_spec["channel"],
            "installPlanApproval": "Automatic",
            "name": package,
            "source": operator_spec["source"],
            "sourceNamespace": "openshift-marketplace",
        },
    }


def subscription_spec_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    keys = ("channel", "installPlanApproval", "name", "source", "sourceNamespace")
    return all(actual.get(key) == expected.get(key) for key in keys)


def operator_spec_by_package(platform: dict[str, Any], package: str) -> dict[str, Any]:
    for operator_spec in platform["operators"]:
        if operator_spec["package"] == package:
            return operator_spec
    raise KeyError(f"Unknown operator package in llm_d platform config: {package}")


def condition_status(resource: dict[str, Any], condition_type: str) -> str | None:
    for condition in resource.get("status", {}).get("conditions", []):
        if condition.get("type") == condition_type:
            return condition.get("status")
    return None


def pvc_access_mode_matches(actual_modes: list[str], expected_mode: str) -> bool:
    return expected_mode in actual_modes


def wait_for_pvc_bound(pvc_name: str, namespace: str, *, timeout_seconds: int) -> dict[str, Any]:
    def _pvc_bound() -> dict[str, Any] | None:
        payload = oc_get_json(
            "persistentvolumeclaim",
            name=pvc_name,
            namespace=namespace,
            ignore_not_found=True,
        )
        if not payload:
            return None
        if payload.get("status", {}).get("phase") == "Bound":
            return payload
        return None

    return wait_until(
        f"persistentvolumeclaim/{pvc_name} bound in {namespace}",
        timeout_seconds=timeout_seconds,
        interval_seconds=5,
        predicate=_pvc_bound,
    )


def wait_for_job_completion(
    job_name: str, namespace: str, *, timeout_seconds: int, interval_seconds: int = 10
) -> dict[str, Any]:
    def _job_completed() -> dict[str, Any] | None:
        payload = oc_get_json(
            "job",
            name=job_name,
            namespace=namespace,
            ignore_not_found=True,
        )
        if not payload:
            return None
        status = payload.get("status", {})
        if status.get("succeeded", 0):
            return payload
        failed_count = status.get("failed", 0)
        for condition in status.get("conditions", []):
            if condition.get("type") == "Failed" and condition.get("status") == "True":
                raise RuntimeError(
                    f"job/{job_name} failed: {condition.get('reason') or 'unknown reason'}"
                )
        if failed_count:
            raise RuntimeError(f"job/{job_name} failed after {failed_count} attempt(s)")
        return None

    return wait_until(
        f"job/{job_name} completion in {namespace}",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        predicate=_job_completed,
    )


def job_pod_names(job_name: str, namespace: str) -> list[str]:
    payload = oc_get_json(
        "pods",
        namespace=namespace,
        selector=f"job-name={job_name}",
        ignore_not_found=True,
    )
    if not payload:
        return []
    return [item["metadata"]["name"] for item in payload.get("items", [])]


def resolve_default_serviceaccount_image_pull_secret(namespace: str) -> str | None:
    payload = oc_get_json(
        "serviceaccount", name="default", namespace=namespace, ignore_not_found=True
    )
    if not payload:
        return None

    for item in payload.get("imagePullSecrets", []):
        name = item.get("name")
        if name:
            return name
    return None


def render_model_cache_job(config: ResolvedConfig, spec: ModelCacheSpec) -> dict[str, Any]:
    common_env = [
        {"name": "MODEL_SOURCE", "value": spec.source_uri},
        {"name": "MODEL_TARGET_DIR", "value": f"/cache/{spec.model_path}"},
        {"name": "MARKER_FILE", "value": spec.marker_path},
        {"name": "CACHE_KEY", "value": spec.cache_key},
    ]
    volumes: list[dict[str, Any]] = [
        {"name": "cache", "persistentVolumeClaim": {"claimName": spec.pvc_name}}
    ]

    if spec.source_scheme == "hf":
        command = """
set -euo pipefail
mkdir -p "${MODEL_TARGET_DIR}"
rm -rf "${MODEL_TARGET_DIR}"/*
python -m pip install --quiet --no-cache-dir 'huggingface_hub[hf_xet]'
python - <<'PY'
import os
from huggingface_hub import snapshot_download

token = None
token_file = os.environ.get("HF_TOKEN_FILE")
if token_file and os.path.exists(token_file):
    with open(token_file, encoding="utf-8") as handle:
        token = handle.read().strip() or None

snapshot_download(
    repo_id=os.environ["MODEL_SOURCE"][5:],
    local_dir=os.environ["MODEL_TARGET_DIR"],
    local_dir_use_symlinks=False,
    token=token,
)
PY
cat > "${MARKER_FILE}" <<EOF
{"source_uri":"${MODEL_SOURCE}","cache_key":"${CACHE_KEY}","scheme":"hf"}
EOF
"""
        volume_mounts = [{"name": "cache", "mountPath": "/cache"}]
        if spec.hf_token_secret_name:
            volumes.append(
                {"name": "hf-token", "secret": {"secretName": spec.hf_token_secret_name}}
            )
            volume_mounts.append(
                {
                    "name": "hf-token",
                    "mountPath": "/var/run/forge/hf-token",
                    "readOnly": True,
                }
            )
            common_env.append(
                {
                    "name": "HF_TOKEN_FILE",
                    "value": f"/var/run/forge/hf-token/{spec.hf_token_secret_key}",
                }
            )

        container = {
            "name": "hf-model-downloader",
            "image": config.model_cache["hf"]["downloader_image"],
            "imagePullPolicy": config.model_cache["download"]["pod_image_pull_policy"],
            "command": ["/bin/bash", "-ceu", command],
            "env": common_env,
            "volumeMounts": volume_mounts,
        }
    elif spec.source_scheme == "oci":
        registry_auth_secret_name = (
            spec.oci_registry_auth_secret_name
            or resolve_default_serviceaccount_image_pull_secret(spec.namespace)
        )
        command = """
set -euo pipefail
mkdir -p "${MODEL_TARGET_DIR}"
rm -rf "${MODEL_TARGET_DIR}"/*
auth_args=()
if [[ -n "${REGISTRY_AUTH_FILE:-}" && -f "${REGISTRY_AUTH_FILE}" ]]; then
  auth_args+=(--registry-config="${REGISTRY_AUTH_FILE}")
fi
oc image extract "${MODEL_SOURCE#oci://}" \
  --path "${OCI_IMAGE_PATH}:${MODEL_TARGET_DIR}" \
  --confirm \
  "${auth_args[@]}"
cat > "${MARKER_FILE}" <<EOF
{"source_uri":"${MODEL_SOURCE}","cache_key":"${CACHE_KEY}","scheme":"oci","image_path":"${OCI_IMAGE_PATH}"}
EOF
"""
        volume_mounts = [{"name": "cache", "mountPath": "/cache"}]
        common_env.append({"name": "OCI_IMAGE_PATH", "value": spec.oci_image_path or "/"})
        if registry_auth_secret_name:
            volumes.append(
                {"name": "registry-auth", "secret": {"secretName": registry_auth_secret_name}}
            )
            volume_mounts.append(
                {
                    "name": "registry-auth",
                    "mountPath": "/var/run/forge/registry-auth",
                    "readOnly": True,
                }
            )
            common_env.append(
                {
                    "name": "REGISTRY_AUTH_FILE",
                    "value": f"/var/run/forge/registry-auth/{spec.oci_registry_auth_secret_key}",
                }
            )

        container = {
            "name": "oci-model-extractor",
            "image": config.model_cache["oci"]["extractor_image"],
            "imagePullPolicy": config.model_cache["download"]["pod_image_pull_policy"],
            "command": ["/bin/bash", "-ceu", command],
            "env": common_env,
            "volumeMounts": volume_mounts,
        }
    else:  # pragma: no cover - guarded by resolve_model_cache
        raise ValueError(f"Unsupported model cache source scheme: {spec.source_scheme}")

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": spec.download_job_name,
            "namespace": spec.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "llm_d",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": config.model_cache["download"]["wait_timeout_seconds"],
            "template": {
                "metadata": {
                    "labels": {
                        "job-name": spec.download_job_name,
                        "app.kubernetes.io/managed-by": "forge",
                        "forge.openshift.io/project": "llm_d",
                    }
                },
                "spec": {
                    "serviceAccountName": "default",
                    "restartPolicy": "Never",
                    "containers": [container],
                    "volumes": volumes,
                },
            },
        },
    }


def model_cache_pvc_ready(spec: ModelCacheSpec) -> bool:
    payload = oc_get_json(
        "persistentvolumeclaim",
        name=spec.pvc_name,
        namespace=spec.namespace,
        ignore_not_found=True,
    )
    if not payload:
        return False

    annotations = payload.get("metadata", {}).get("annotations", {})
    return (
        annotations.get("forge.openshift.io/model-cache-ready") == "true"
        and annotations.get("forge.openshift.io/model-cache-key") == spec.cache_key
        and annotations.get("forge.openshift.io/model-source-uri") == spec.source_uri
    )


def annotate_model_cache_pvc(spec: ModelCacheSpec) -> None:
    oc(
        "annotate",
        "persistentvolumeclaim",
        spec.pvc_name,
        "-n",
        spec.namespace,
        "--overwrite",
        "forge.openshift.io/model-cache-ready=true",
        f"forge.openshift.io/model-cache-key={spec.cache_key}",
        f"forge.openshift.io/model-source-uri={spec.source_uri}",
        f"forge.openshift.io/model-uri={spec.model_uri}",
    )
