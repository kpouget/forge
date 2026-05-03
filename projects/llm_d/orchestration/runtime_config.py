from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from projects.core.library import config, env, run

LOGGER = logging.getLogger(__name__)
ORCHESTRATION_DIR = env.FORGE_HOME / "projects" / "llm_d" / "orchestration"
CONFIG_DIR = ORCHESTRATION_DIR


@dataclass(frozen=True)
class ResolvedConfig:
    artifact_dir: Path
    project_root: Path
    config_dir: Path
    preset_name: str
    preset_alias: str | None
    job_name: str
    namespace: str
    namespace_is_managed: bool
    gpu_count: int | None
    platform: dict[str, Any]
    model_key: str
    model: dict[str, Any]
    scheduler_profile_key: str
    scheduler_profile: dict[str, Any] | None
    model_cache: dict[str, Any]
    smoke_request: dict[str, Any]
    benchmark: dict[str, Any] | None
    fournos_config: dict[str, Any]
    overrides: dict[str, Any]

    @property
    def manifests_dir(self) -> Path:
        return self.config_dir / "manifests"


@dataclass(frozen=True)
class ModelCacheSpec:
    source_uri: str
    source_scheme: str
    cache_key: str
    namespace: str
    pvc_name: str
    pvc_size: str
    access_mode: str
    storage_class_name: str | None
    model_path: str
    model_uri: str
    marker_filename: str
    download_job_name: str
    hf_token_secret_name: str | None
    hf_token_secret_key: str | None
    oci_image_path: str | None
    oci_registry_auth_secret_name: str | None
    oci_registry_auth_secret_key: str | None

    @property
    def marker_path(self) -> str:
        return f"/cache/{self.model_path}/{self.marker_filename}"


def init() -> Path:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    env.init()
    run.init()
    ensure_artifact_directories(env.ARTIFACT_DIR)
    return env.ARTIFACT_DIR


def ensure_artifact_directories(artifact_dir: Path) -> None:
    for relative in ("src", "artifacts", "artifacts/results"):
        (artifact_dir / relative).mkdir(parents=True, exist_ok=True)


def load_run_configuration(
    *, cwd: Path | None = None, artifact_dir: Path | None = None
) -> ResolvedConfig:
    cwd = cwd or Path.cwd()
    if artifact_dir is not None:
        os.environ["ARTIFACT_DIR"] = str(artifact_dir)
    artifact_dir = init()
    _reinitialize_project_config()

    platform_data = copy.deepcopy(config.project.get_config("platform"))
    model_cache = copy.deepcopy(config.project.get_config("model_cache"))
    fournos_config = load_fournos_config(cwd)
    overrides = parse_overrides(
        os.environ.get("FORGE_CONFIG_OVERRIDES", ""),
        allowed_keys=config.project.get_config("runtime.allowed_override_keys", []),
    )

    requested_preset = (
        fournos_config.get("preset")
        or os.environ.get("FORGE_PRESET")
        or config.project.get_config("runtime.default_preset")
    )
    apply_requested_preset(requested_preset)

    preset_name = config.project.get_config("runtime.selected_preset")
    preset_alias = requested_preset if requested_preset != preset_name else None

    model_name = config.project.get_config("runtime.model_key")
    model = copy.deepcopy(config.project.get_config(f"models.{model_name}"))

    scheduler_profile_key = config.project.get_config("runtime.scheduler_profile_key")
    scheduler_profile = None
    if scheduler_profile_key != "default":
        scheduler_profile = copy.deepcopy(
            config.project.get_config(f"scheduler_profiles.{scheduler_profile_key}")
        )

    smoke_request_name = config.project.get_config("runtime.smoke_request_key")
    smoke_request = copy.deepcopy(
        config.project.get_config(f"workloads.smoke_requests.{smoke_request_name}")
    )

    benchmark_name = config.project.get_config("runtime.benchmark_key", None)
    benchmark = None
    if benchmark_name:
        benchmark = copy.deepcopy(
            config.project.get_config(f"workloads.benchmarks.{benchmark_name}")
        )

    job_name = fournos_config.get("job-name") or os.environ.get("FORGE_JOB_NAME")
    if not job_name:
        job_name = f"local-{preset_name}"

    namespace_override = overrides.get("namespace") or fournos_config.get("namespace")
    default_namespace = platform_data["cluster"].get("namespace_name")
    namespace = (
        namespace_override
        or default_namespace
        or derive_namespace(
            job_name,
            platform_data["cluster"]["namespace_prefix"],
            platform_data["cluster"]["namespace_max_length"],
        )
    )

    gpu_count = normalize_gpu_count(fournos_config.get("gpu-count"))

    return ResolvedConfig(
        artifact_dir=Path(artifact_dir),
        project_root=env.FORGE_HOME,
        config_dir=ORCHESTRATION_DIR,
        preset_name=preset_name,
        preset_alias=preset_alias,
        job_name=job_name,
        namespace=namespace,
        namespace_is_managed=namespace_override is None and default_namespace is None,
        gpu_count=gpu_count,
        platform=platform_data,
        model_key=model_name,
        model=model,
        scheduler_profile_key=scheduler_profile_key,
        scheduler_profile=scheduler_profile,
        model_cache=model_cache,
        smoke_request=smoke_request,
        benchmark=benchmark,
        fournos_config=fournos_config,
        overrides=overrides,
    )


def _reinitialize_project_config() -> None:
    config.project = None
    artifact_config = env.ARTIFACT_DIR / "config.yaml"
    if artifact_config.exists():
        artifact_config.unlink()

    presets_applied = env.ARTIFACT_DIR / "presets_applied"
    if presets_applied.exists():
        presets_applied.unlink()

    config.init(ORCHESTRATION_DIR)


def apply_requested_preset(requested_preset: str) -> None:
    if not config.project.get_preset(requested_preset):
        raise ValueError(f"Unknown llm_d preset: {requested_preset}")

    config.project.apply_preset(requested_preset)


def load_fournos_config(cwd: Path) -> dict[str, Any]:
    config_path = cwd / "fournos_config.yaml"
    if not config_path.exists():
        return {}

    data = load_yaml(config_path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected FOURNOS config type in {config_path}: {type(data)}")
    return data


def parse_overrides(raw: str, *, allowed_keys: Iterable[str]) -> dict[str, Any]:
    if not raw or raw.strip() in {"", "null", "{}"}:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"FORGE_CONFIG_OVERRIDES is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("FORGE_CONFIG_OVERRIDES must decode to a JSON object")

    allowed_keys = frozenset(allowed_keys)
    unsupported = sorted(set(data) - allowed_keys)
    if unsupported:
        raise ValueError(
            "Unsupported llm_d override keys: "
            f"{', '.join(unsupported)}. Allowed keys: {', '.join(sorted(allowed_keys))}"
        )

    return data


def normalize_gpu_count(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        LOGGER.warning("Ignoring invalid gpu-count value: %s", value)
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


def slugify_identifier(value: str, *, max_length: int = 63) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:max_length].rstrip("-") or "item"


def truncate_k8s_name(value: str, *, max_length: int = 63) -> str:
    return value[:max_length].rstrip("-")


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers[:3])


def resolve_model_cache(config: ResolvedConfig) -> ModelCacheSpec | None:
    if not config.model_cache.get("enabled", False):
        return None

    source_uri = config.model["uri"]
    if source_uri.startswith(("pvc://", "pvc+hf://")):
        return None

    if source_uri.startswith("hf://"):
        source_scheme = "hf"
    elif source_uri.startswith("oci://"):
        source_scheme = "oci"
    else:
        raise ValueError(f"Unsupported model cache source URI for {config.model_key}: {source_uri}")

    model_cache_overrides = config.model.get("cache", {})
    pvc_defaults = config.model_cache["pvc"]
    pvc_prefix = config.model_cache["pvc"]["name_prefix"]
    cache_key = hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:10]
    pvc_name = truncate_k8s_name(
        f"{pvc_prefix}-{slugify_identifier(config.model_key, max_length=32)}-{cache_key}"
    )
    model_path = pvc_defaults["model_directory_name"]

    return ModelCacheSpec(
        source_uri=source_uri,
        source_scheme=source_scheme,
        cache_key=cache_key,
        namespace=config.namespace,
        pvc_name=pvc_name,
        pvc_size=model_cache_overrides.get("pvc_size", pvc_defaults["size"]),
        access_mode=model_cache_overrides.get("access_mode", pvc_defaults["access_mode"]),
        storage_class_name=model_cache_overrides.get(
            "storage_class_name", pvc_defaults.get("storage_class_name")
        ),
        model_path=model_path,
        model_uri=f"pvc://{pvc_name}/{model_path}",
        marker_filename=config.model_cache["marker_filename"],
        download_job_name=truncate_k8s_name(f"{pvc_name}-download"),
        hf_token_secret_name=model_cache_overrides.get(
            "hf_token_secret_name", config.model_cache["hf"].get("token_secret_name")
        ),
        hf_token_secret_key=config.model_cache["hf"].get("token_secret_key"),
        oci_image_path=model_cache_overrides.get(
            "oci_image_path", config.model_cache["oci"].get("image_path")
        ),
        oci_registry_auth_secret_name=model_cache_overrides.get(
            "oci_registry_auth_secret_name",
            config.model_cache["oci"].get("registry_auth_secret_name"),
        ),
        oci_registry_auth_secret_key=config.model_cache["oci"].get("registry_auth_secret_key"),
    )


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_yaml(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
