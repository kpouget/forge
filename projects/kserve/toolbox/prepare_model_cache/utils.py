from __future__ import annotations

from pathlib import Path
from typing import Any

from projects.core.dsl.utils.k8s import oc, oc_get_json
from projects.llm_d.runtime.runtime_config import ModelCacheSpec, ResolvedConfig


def pvc_access_mode_matches(actual_modes: list[str], expected_mode: str) -> bool:
    return expected_mode in actual_modes


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


def load_runtime_script(name: str) -> str:
    # Load scripts from the llm_d runtime scripts directory
    script_path = (
        Path(__file__).resolve().parent.parent.parent.parent.parent
        / "llm_d"
        / "runtime"
        / "scripts"
        / name
    )
    return script_path.read_text(encoding="utf-8")


def resolve_default_serviceaccount_image_pull_secret(namespace: str) -> str | None:
    payload = oc_get_json(
        "serviceaccount", name="default", namespace=namespace, ignore_not_found=True
    )
    if not payload:
        return None

    for pull_secret in payload.get("imagePullSecrets", []):
        secret_name = pull_secret.get("name")
        if secret_name and secret_name.startswith("default-dockercfg-"):
            return secret_name

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
        command = load_runtime_script("download_hf_model.sh")
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
        command = load_runtime_script("extract_oci_model.sh")
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


def render_model_cache_pvc(spec: ModelCacheSpec) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": spec.pvc_name,
            "namespace": spec.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "llm_d",
                "forge.openshift.io/model-cache": "true",
                "forge.openshift.io/preserve": "true",
            },
            "annotations": {
                "forge.openshift.io/model-cache-key": spec.cache_key,
                "forge.openshift.io/model-source-uri": spec.source_uri,
            },
        },
        "spec": {
            "accessModes": [spec.access_mode],
            "resources": {"requests": {"storage": spec.pvc_size}},
        },
    }
    if spec.storage_class_name:
        manifest["spec"]["storageClassName"] = spec.storage_class_name
    return manifest
