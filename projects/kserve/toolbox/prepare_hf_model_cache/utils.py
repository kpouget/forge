from __future__ import annotations

from pathlib import Path
from typing import Any

from projects.core.dsl.utils.k8s import oc, oc_get_json


def pvc_access_mode_matches(actual_modes: list[str], expected_mode: str) -> bool:
    return expected_mode in actual_modes


def annotate_model_cache_pvc(spec: dict[str, Any]) -> None:
    oc(
        "annotate",
        "persistentvolumeclaim",
        spec["pvc_name"],
        "-n",
        spec["namespace"],
        f"forge.openshift.io/model-cache-key={spec['cache_key']}",
        f"forge.openshift.io/model-source-uri={spec['source_uri']}",
        f"forge.openshift.io/model-source-scheme={spec['source_scheme']}",
        "--overwrite",
    )


def model_cache_pvc_ready(spec: dict[str, Any]) -> bool:
    """Check if the model cache PVC is ready (contains the marker file)."""
    if spec["source_scheme"] == "hf":
        return _hf_cache_ready(spec)
    return False


def _hf_cache_ready(spec: dict[str, Any]) -> bool:
    import json

    pod_spec = {
        "restartPolicy": "Never",
        "containers": [
            {
                "name": "check",
                "image": "registry.access.redhat.com/ubi9/ubi-minimal:9.5",
                "command": ["test", "-f", spec["marker_path"]],
                "volumeMounts": [{"name": "cache", "mountPath": "/cache"}],
            }
        ],
        "volumes": [
            {
                "name": "cache",
                "persistentVolumeClaim": {"claimName": spec["pvc_name"]},
            }
        ],
    }

    check_result = oc(
        "run",
        f"{spec['pvc_name']}-ready-check",
        "--image=registry.access.redhat.com/ubi9/ubi-minimal:9.5",
        f"--overrides={json.dumps(pod_spec)}",
        f"-n={spec['namespace']}",
        "--restart=Never",
        "--attach",
        "--quiet",
        check=False,
        capture_output=True,
    )

    # Clean up the pod
    oc(
        "delete",
        "pod",
        f"{spec['pvc_name']}-ready-check",
        "-n",
        spec["namespace"],
        "--ignore-not-found=true",
        check=False,
    )

    return check_result.returncode == 0


def load_runtime_script(name: str) -> str:
    # Load scripts from the local toolbox scripts directory
    script_path = Path(__file__).resolve().parent / "scripts" / name
    return script_path.read_text(encoding="utf-8")


def render_model_cache_pvc(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": spec["pvc_name"],
            "namespace": spec["namespace"],
        },
        "spec": {
            "accessModes": [spec["access_mode"]],
            "resources": {"requests": {"storage": spec["pvc_size"]}},
            **(
                {"storageClassName": spec["storage_class_name"]}
                if spec["storage_class_name"]
                else {}
            ),
        },
    }


def render_hf_model_cache_job(
    args, spec: dict[str, Any], hf_secret_name: str | None = None
) -> dict[str, Any]:
    common_env = [
        {"name": "MODEL_SOURCE", "value": spec["source_uri"]},
        {"name": "MODEL_TARGET_DIR", "value": f"/cache/{spec['model_path']}"},
        {"name": "MARKER_FILE", "value": spec["marker_path"]},
        {"name": "CACHE_KEY", "value": spec["cache_key"]},
    ]

    volume_mounts = [{"name": "cache", "mountPath": "/cache"}]
    volumes = [
        {
            "name": "cache",
            "persistentVolumeClaim": {"claimName": spec["pvc_name"]},
        }
    ]

    # Add HF token secret if available (either passed or from spec)
    effective_secret_name = hf_secret_name or spec["hf_token_secret_name"]
    if effective_secret_name:
        common_env.append(
            {
                "name": "HF_TOKEN_FILE",
                "value": "/secrets/hf-token/token",  # Use 'token' key for vault-created secrets
            }
        )
        volume_mounts.append(
            {
                "name": "hf-token",
                "mountPath": "/secrets/hf-token",
                "readOnly": True,
            }
        )
        volumes.append(
            {
                "name": "hf-token",
                "secret": {"secretName": effective_secret_name},
            }
        )

    command = load_runtime_script("download_hf_model.sh")

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": spec["download_job_name"],
            "namespace": spec["namespace"],
        },
        "spec": {
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "download",
                            "image": args.downloader_image,
                            "imagePullPolicy": args.pod_image_pull_policy,
                            "command": ["/bin/bash", "-c", command],
                            "env": common_env,
                            "volumeMounts": volume_mounts,
                        }
                    ],
                    "volumes": volumes,
                }
            }
        },
    }


def job_pod_names(job_name: str, namespace: str) -> list[str]:
    """Get the names of pods created by a Job.

    Args:
        job_name: Job name
        namespace: Namespace

    Returns:
        List of pod names
    """
    payload = oc_get_json(
        "pods",
        namespace=namespace,
        selector=f"job-name={job_name}",
        ignore_not_found=True,
    )
    if not payload:
        return []
    return [item["metadata"]["name"] for item in payload.get("items", [])]
