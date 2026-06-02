"""
Utilities for the deploy LLM inference service toolbox module.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from projects.llm_d.orchestration.runtime_config import load_yaml, resolve_model_cache_spec


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

    cache_spec = resolve_model_cache_spec(
        namespace=namespace,
        model_key=model_key,
        model=model,
        model_cache=model_cache,
    )
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
