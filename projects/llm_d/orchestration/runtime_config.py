from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any

from projects.core.library import config, env, run

logger = logging.getLogger(__name__)
RUNTIME_DIR = Path(__file__).resolve().parent
PROJECT_DIR = RUNTIME_DIR.parent
ORCHESTRATION_DIR = PROJECT_DIR / "orchestration"
CONFIG_DIR = ORCHESTRATION_DIR


def init() -> Path:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    env.init()
    run.init()
    if config.project is None:
        # Load llm_d project config when runtime is used without orchestration preparation.
        config.init(CONFIG_DIR)
    ensure_artifact_directories(env.ARTIFACT_DIR)
    return env.ARTIFACT_DIR


def ensure_artifact_directories(artifact_dir: Path) -> None:
    for relative in ("src", "artifacts", "artifacts/results"):
        (artifact_dir / relative).mkdir(parents=True, exist_ok=True)


# Configuration accessor functions


def get_config_dir() -> Path:
    """Get the LLM-D configuration directory"""
    return ORCHESTRATION_DIR


def get_namespace() -> str:
    """Get the resolved namespace for this execution"""

    platform_data = get_platform_config()
    namespace_override = config.project.get_config("runtime.namespace_override", None)
    namespace_config = platform_data["cluster"]["namespace"]
    default_namespace = namespace_config.get("name")

    if namespace_override:
        return namespace_override
    if default_namespace:
        return default_namespace

    # Derive namespace from job name
    job_name = get_job_name()
    return derive_namespace(
        job_name,
        namespace_config["prefix"],
        namespace_config["max_length"],
    )


def get_namespace_is_managed() -> bool:
    """Check if namespace is managed (auto-derived) vs explicitly configured"""

    namespace_override = config.project.get_config("runtime.namespace_override", None)
    platform_data = get_platform_config()
    default_namespace = platform_data["cluster"]["namespace"].get("name")

    return namespace_override is None and default_namespace is None


def get_job_name() -> str:
    """Get the resolved job name"""

    job_name = config.project.get_config("runtime.job_name", None)
    if job_name:
        return job_name

    preset_name = config.project.get_config("runtime.selected_preset")
    return f"local-{preset_name}"


def get_model_key() -> str:
    """Get the selected model key"""

    return config.project.get_config("runtime.model_key")


def get_model() -> dict[str, Any]:
    """Get the resolved model configuration"""

    model_key = get_model_key()
    return copy.deepcopy(config.project.get_config(f"models.{model_key}"))


def get_platform_config() -> dict[str, Any]:
    """Get the normalized platform configuration"""

    return normalize_platform_config(copy.deepcopy(config.project.get_config("platform")))


def get_model_cache_config() -> dict[str, Any]:
    """Get the model cache configuration"""

    return copy.deepcopy(config.project.get_config("model_cache"))


def get_benchmark_config() -> dict[str, Any] | None:
    """Get the benchmark configuration if specified"""

    benchmark_name = config.project.get_config("runtime.benchmark_key", None)
    if not benchmark_name:
        return None
    return copy.deepcopy(config.project.get_config(f"workloads.benchmarks.{benchmark_name}"))


def get_scheduler_profile() -> dict[str, Any] | None:
    """Get the scheduler profile configuration"""

    profile_key = config.project.get_config("runtime.scheduler_profile_key")
    if profile_key == "default":
        return None
    return copy.deepcopy(config.project.get_config(f"scheduler_profiles.{profile_key}"))


def get_gpu_count() -> int | None:
    """Get the normalized GPU count"""

    return normalize_gpu_count(config.project.get_config("runtime.gpu_count", None))


def get_scheduler_profile_key() -> str:
    """Get the selected scheduler profile key"""

    return config.project.get_config("runtime.scheduler_profile_key")


def get_smoke_request() -> dict[str, Any]:
    """Get the smoke request configuration"""

    smoke_request_key = config.project.get_config("runtime.smoke_request_key")
    return copy.deepcopy(config.project.get_config(f"workloads.smoke_requests.{smoke_request_key}"))


def normalize_platform_config(platform_data: dict[str, Any]) -> dict[str, Any]:
    cluster = platform_data["cluster"]
    if "namespace" not in cluster:
        cluster["namespace"] = {
            "name": cluster.pop("namespace_name", None),
            "prefix": cluster.pop("namespace_prefix"),
            "max_length": cluster.pop("namespace_max_length"),
        }

    operators = platform_data["operators"]
    if isinstance(operators, list):
        platform_data["operators"] = {
            operator_spec["package"]: {
                key: value for key, value in operator_spec.items() if key != "package"
            }
            for operator_spec in operators
        }

    return platform_data


def normalize_gpu_count(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid gpu-count value: %s", value)
        return None


def derive_namespace(job_name: str, prefix: str, max_length: int) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", job_name.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "run"

    if slug.startswith(f"{prefix}-"):
        namespace = slug
    else:
        namespace = f"{prefix}-{slug}"

    namespace = namespace[:max_length].rstrip("-")
    if not namespace:
        raise ValueError(f"Could not derive a valid namespace from job name: {job_name}")
    return namespace


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers[:3])
