from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from projects.core.library import config, env, run

LOGGER = logging.getLogger(__name__)
ORCHESTRATION_DIR = env.FORGE_HOME / "projects" / "llm_d" / "orchestration"
CONFIG_DIR = ORCHESTRATION_DIR


class CommandError(RuntimeError):
    """Raised when an external command exits unsuccessfully."""


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
    scheduler_profile: dict[str, Any]
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


def apply_manifest(artifact_path: Path, manifest: dict[str, Any]) -> None:
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
    def _namespace_gone() -> bool:
        return not resource_exists("namespace", namespace)

    wait_until(
        f"namespace/{namespace} deletion",
        timeout_seconds=timeout_seconds,
        interval_seconds=10,
        predicate=_namespace_gone,
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
        label_args = [f"{key}={value}" for key, value in labels.items()]
        oc("label", "namespace", namespace, "--overwrite", *label_args)


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


def load_manifest_template(config: ResolvedConfig, relative_path: str) -> dict[str, Any]:
    return load_yaml(config.config_dir / relative_path)


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers[:3])


def condition_status(resource: dict[str, Any], condition_type: str) -> str | None:
    conditions = resource.get("status", {}).get("conditions", [])
    for condition in conditions:
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

    container: dict[str, Any]
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
                {
                    "name": "hf-token",
                    "secret": {"secretName": spec.hf_token_secret_name},
                }
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
                {
                    "name": "registry-auth",
                    "secret": {"secretName": registry_auth_secret_name},
                }
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
