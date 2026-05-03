from __future__ import annotations

import copy
import json
from typing import Any

from projects.llm_d.orchestration.runtime_config import (
    ModelCacheSpec,
    ResolvedConfig,
    load_yaml,
    resolve_model_cache,
)


def load_manifest_template(config: ResolvedConfig, relative_path: str) -> dict[str, Any]:
    return load_yaml(config.config_dir / relative_path)


def render_datasciencecluster(config: ResolvedConfig) -> dict[str, Any]:
    template_path = config.config_dir / config.platform["rhoai"]["datasciencecluster_template"]
    manifest = load_yaml(template_path)
    manifest["metadata"]["name"] = config.platform["rhoai"]["datasciencecluster_name"]
    manifest["metadata"]["namespace"] = config.platform["rhoai"]["namespace"]
    return manifest


def render_gateway(config: ResolvedConfig) -> dict[str, Any]:
    template_path = config.config_dir / config.platform["gateway"]["manifest_template"]
    manifest = load_yaml(template_path)
    manifest["metadata"]["name"] = config.platform["gateway"]["name"]
    manifest["metadata"]["namespace"] = config.platform["gateway"]["namespace"]
    manifest["spec"]["gatewayClassName"] = config.platform["gateway"]["gateway_class_name"]
    return manifest


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


def render_inference_service(config: ResolvedConfig) -> dict[str, Any]:
    template_path = config.config_dir / config.platform["inference_service"]["template"]
    manifest = load_yaml(template_path)

    name = config.platform["inference_service"]["name"]
    manifest["metadata"]["name"] = name
    manifest["metadata"]["namespace"] = config.namespace
    manifest["metadata"].setdefault("labels", {})
    manifest["metadata"]["labels"].update(
        {
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        }
    )

    cache_spec = resolve_model_cache(config)
    manifest["spec"]["model"]["uri"] = cache_spec.model_uri if cache_spec else config.model["uri"]
    manifest["spec"]["model"]["name"] = config.model["served_model_name"]
    manifest["spec"]["template"]["containers"][0]["resources"] = copy.deepcopy(
        config.model["resources"]
    )

    if config.scheduler_profile_key == "default":
        manifest["spec"]["router"]["scheduler"] = {}
        return manifest

    if config.scheduler_profile is None:
        raise ValueError(f"Missing scheduler profile config for {config.scheduler_profile_key}")

    scheduler_profile_path = config.config_dir / config.scheduler_profile["config_path"]
    scheduler_profile_config = scheduler_profile_path.read_text(encoding="utf-8")
    router_args = manifest["spec"]["router"]["scheduler"]["template"]["containers"][0]["args"]
    if not router_args or router_args[-1] != "--config-text":
        raise ValueError("Expected llm-d router args to end with --config-text")
    router_args.append(scheduler_profile_config)

    return manifest


def render_smoke_request_job(
    config: ResolvedConfig, endpoint_url: str, payload: dict[str, Any]
) -> dict[str, Any]:
    smoke = config.platform["smoke"]
    command = """
set -eu
attempt=1
while [ "${attempt}" -le "${REQUEST_RETRIES}" ]; do
  if curl -k -sSf --max-time "${REQUEST_TIMEOUT_SECONDS}" \
    "${ENDPOINT_URL}${ENDPOINT_PATH}" \
    -H "Content-Type: application/json" \
    -d "${REQUEST_PAYLOAD}" \
    -o /tmp/smoke-response.json \
    2>/tmp/smoke-error.log; then
    cat /tmp/smoke-response.json
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep "${REQUEST_RETRY_DELAY_SECONDS}"
done
cat /tmp/smoke-error.log >&2 || true
exit 1
"""

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": smoke["job_name"],
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "llm_d",
                "forge.openshift.io/component": "smoke",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": (
                smoke["request_retries"]
                * (smoke["request_timeout_seconds"] + smoke["request_retry_delay_seconds"])
            ),
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/managed-by": "forge",
                        "forge.openshift.io/project": "llm_d",
                        "forge.openshift.io/component": "smoke",
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "smoke",
                            "image": smoke["client_image"],
                            "command": ["/bin/sh", "-ceu", command],
                            "env": [
                                {"name": "ENDPOINT_URL", "value": endpoint_url},
                                {"name": "ENDPOINT_PATH", "value": smoke["endpoint_path"]},
                                {"name": "REQUEST_PAYLOAD", "value": json.dumps(payload)},
                                {"name": "REQUEST_RETRIES", "value": str(smoke["request_retries"])},
                                {
                                    "name": "REQUEST_RETRY_DELAY_SECONDS",
                                    "value": str(smoke["request_retry_delay_seconds"]),
                                },
                                {
                                    "name": "REQUEST_TIMEOUT_SECONDS",
                                    "value": str(smoke["request_timeout_seconds"]),
                                },
                            ],
                        }
                    ],
                },
            },
        },
    }


def render_guidellm_pvc(config: ResolvedConfig) -> dict[str, Any]:
    if not config.benchmark:
        raise ValueError("Benchmark configuration is not enabled for this preset")

    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": config.benchmark["job_name"],
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "llm_d",
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": config.benchmark["pvc_size"]}},
        },
    }


def render_guidellm_job(config: ResolvedConfig, endpoint_url: str) -> dict[str, Any]:
    if not config.benchmark:
        raise ValueError("Benchmark configuration is not enabled for this preset")

    args = [
        "benchmark",
        "run",
        f"--target={endpoint_url}",
        f"--rate={config.benchmark['rate']}",
    ]
    for key, value in config.benchmark["args"].items():
        if value is None:
            continue
        args.append(f"--{key.replace('_', '-')}={value}")
    args.append("--outputs=json")

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": config.benchmark["job_name"],
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "llm_d",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/managed-by": "forge",
                        "forge.openshift.io/project": "llm_d",
                    }
                },
                "spec": {
                    "serviceAccountName": "default",
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "guidellm",
                            "image": config.benchmark["image"],
                            "command": ["/opt/app-root/bin/guidellm"],
                            "args": args,
                            "env": [{"name": "USER", "value": "guidellm"}],
                            "volumeMounts": [
                                {"name": "home", "mountPath": "/home/guidellm"},
                                {"name": "results", "mountPath": "/results"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "home", "emptyDir": {}},
                        {
                            "name": "results",
                            "persistentVolumeClaim": {"claimName": config.benchmark["job_name"]},
                        },
                    ],
                },
            },
        },
    }


def render_guidellm_copy_pod(
    config: ResolvedConfig, node_name: str | None = None
) -> dict[str, Any]:
    if not config.benchmark:
        raise ValueError("Benchmark configuration is not enabled for this preset")

    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"{config.benchmark['job_name']}-copy",
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "llm_d",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "initContainers": [
                {
                    "name": "permission-fixer",
                    "image": config.benchmark["image"],
                    "command": [
                        "/bin/sh",
                        "-c",
                        "chmod 755 /results && chown -R 1001:1001 /results || true",
                    ],
                    "securityContext": {
                        "runAsUser": 0,
                        "allowPrivilegeEscalation": True,
                    },
                    "volumeMounts": [{"name": "results", "mountPath": "/results"}],
                }
            ],
            "containers": [
                {
                    "name": "copy-helper",
                    "image": config.benchmark["image"],
                    "command": ["/bin/sleep", "300"],
                    "securityContext": {
                        "runAsUser": 1001,
                        "runAsNonRoot": True,
                        "allowPrivilegeEscalation": False,
                    },
                    "volumeMounts": [{"name": "results", "mountPath": "/results"}],
                }
            ],
            "volumes": [
                {
                    "name": "results",
                    "persistentVolumeClaim": {"claimName": config.benchmark["job_name"]},
                }
            ],
        },
    }
    if node_name:
        pod["spec"]["nodeName"] = node_name
    return pod
