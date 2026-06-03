"""
Utilities for the deploy LLM inference service toolbox module.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import yaml

from projects.core.dsl.utils import slugify_identifier, truncate_k8s_name


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def render_inference_service_from_parts(
    *,
    config_dir: str,
    namespace: str,
    inference_service: dict[str, Any],
    model_key: str,
    model: dict[str, Any],
    scheduler_profile_key: str,
    scheduler_profile: dict[str, Any] | None,
    model_cache: dict[str, Any],
) -> dict[str, Any]:
    """Render an LLM inference service manifest from individual components.

    Args:
        config_dir: Configuration directory path
        namespace: Target namespace
        inference_service: Inference service configuration
        model_key: Model key identifier
        model: Model configuration
        scheduler_profile_key: Scheduler profile key
        scheduler_profile: Scheduler profile configuration
        model_cache: Model cache configuration

    Returns:
        InferenceService manifest as dict
    """
    template_path = Path(config_dir) / inference_service["template"]
    manifest = load_yaml(template_path)

    name = inference_service["name"]
    manifest["metadata"]["name"] = name
    manifest["metadata"]["namespace"] = namespace
    manifest["metadata"].setdefault("labels", {})
    manifest["metadata"]["labels"].update(
        {
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        }
    )

    # Resolve model cache spec inline
    cache_spec = None
    if model_cache.get("enabled", False):
        source_uri = model["uri"]
        if not source_uri.startswith(("pvc://", "pvc+hf://")):
            if source_uri.startswith("hf://"):
                source_scheme = "hf"
            elif source_uri.startswith("oci://"):
                source_scheme = "oci"
            else:
                raise ValueError(
                    f"Unsupported model cache source URI for {model_key}: {source_uri}"
                )

            model_cache_overrides = model.get("cache", {})
            pvc_defaults = model_cache["pvc"]
            pvc_prefix = model_cache["pvc"]["name_prefix"]
            cache_key = hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:10]
            pvc_name = truncate_k8s_name(
                f"{pvc_prefix}-{slugify_identifier(model_key, max_length=32)}-{cache_key}"
            )
            model_path = pvc_defaults["model_directory_name"]

            cache_spec = {
                "source_uri": source_uri,
                "source_scheme": source_scheme,
                "cache_key": cache_key,
                "namespace": namespace,
                "pvc_name": pvc_name,
                "pvc_size": model_cache_overrides.get("pvc_size", pvc_defaults["size"]),
                "access_mode": model_cache_overrides.get(
                    "access_mode", pvc_defaults["access_mode"]
                ),
                "storage_class_name": model_cache_overrides.get(
                    "storage_class_name", pvc_defaults.get("storage_class_name")
                ),
                "model_path": model_path,
                "model_uri": f"pvc://{pvc_name}/{model_path}",
                "marker_filename": model_cache["marker_filename"],
                "marker_path": f"/cache/{model_path}/{model_cache['marker_filename']}",
                "download_job_name": truncate_k8s_name(f"{pvc_name}-download"),
                "hf_token_secret_name": model_cache_overrides.get(
                    "hf_token_secret_name", model_cache["hf"].get("token_secret_name")
                ),
                "hf_token_secret_key": model_cache["hf"].get("token_secret_key"),
            }

    manifest["spec"]["model"]["uri"] = cache_spec["model_uri"] if cache_spec else model["uri"]
    manifest["spec"]["model"]["name"] = model["served_model_name"]
    manifest["spec"]["template"]["containers"][0]["resources"] = copy.deepcopy(model["resources"])

    if scheduler_profile_key == "default":
        manifest["spec"]["router"]["scheduler"] = {}
        return manifest

    if scheduler_profile is None:
        raise ValueError(f"Missing scheduler profile config for {scheduler_profile_key}")

    scheduler_profile_path = Path(config_dir) / scheduler_profile["config_path"]
    scheduler_profile_config = scheduler_profile_path.read_text(encoding="utf-8")
    router_args = manifest["spec"]["router"]["scheduler"]["template"]["containers"][0]["args"]
    if not router_args or router_args[-1] != "--config-text":
        raise ValueError("Expected llm-d router args to end with --config-text")
    router_args.append(scheduler_profile_config)

    return manifest
