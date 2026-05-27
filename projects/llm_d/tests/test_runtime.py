from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from projects.cluster.toolbox.cluster_deploy_operator import main as cluster_deploy_operator
from projects.cluster.toolbox.deploy_custom_catalog import main as deploy_custom_catalog
from projects.core.library import config as forge_config
from projects.llm_d.orchestration import ci as llmd_ci
from projects.llm_d.orchestration import cleanup_phase as cleanup_toolbox
from projects.llm_d.orchestration import cli as llmd_cli
from projects.llm_d.orchestration import configuration as llmd_configuration
from projects.llm_d.orchestration import prepare_phase as prepare_toolbox
from projects.llm_d.orchestration import test_phase as test_toolbox
from projects.llm_d.runtime import llmd_runtime, phase_inputs
from projects.llm_d.toolbox.apply_datasciencecluster import main as apply_datasciencecluster_toolbox
from projects.llm_d.toolbox.bootstrap_gpu_clusterpolicy import main as bootstrap_gpu_clusterpolicy
from projects.llm_d.toolbox.bootstrap_nfd_instance import main as bootstrap_nfd_instance
from projects.llm_d.toolbox.deploy_llmisvc import main as deploy_llmisvc_toolbox
from projects.llm_d.toolbox.ensure_gateway import main as ensure_gateway_toolbox
from projects.llm_d.toolbox.prepare_model_cache import main as prepare_model_cache_toolbox
from projects.llm_d.toolbox.run_guidellm_benchmark import main as run_guidellm_benchmark_toolbox
from projects.llm_d.toolbox.run_smoke_request import main as run_smoke_request_toolbox
from projects.llm_d.toolbox.wait_datasciencecluster_ready import (
    main as wait_datasciencecluster_ready_toolbox,
)


def _load_runtime_configuration(
    tmp_path: Path,
    *,
    requested_preset: str | None = None,
    config_overrides: dict[str, object] | None = None,
    job_name: str | None = None,
):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    if config_overrides:
        llmd_runtime.write_yaml(
            artifact_dir / forge_config.VARIABLE_OVERRIDES_FILENAME,
            config_overrides,
        )
    return (
        llmd_configuration.load_runtime_configuration(
            cwd=tmp_path,
            artifact_dir=artifact_dir,
            requested_preset=requested_preset,
            job_name=job_name,
        ),
        artifact_dir,
    )


def test_derive_namespace_uses_prefix_once() -> None:
    namespace = llmd_runtime.derive_namespace("llm-d-nightly-smoke", "llm-d", 63)
    assert namespace == "llm-d-nightly-smoke"


def test_load_runtime_configuration_rejects_unknown_variable_override_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Config key 'model' does not exist"):
        _load_runtime_configuration(tmp_path, config_overrides={"model": "other"})


def test_load_run_configuration_resolves_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fournos_config = tmp_path / "fournos_config.yaml"
    fournos_config.write_text(
        "preset: cks\njob-name: llm-d-e2e\n",
        encoding="utf-8",
    )

    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    assert config.preset_name == "smoke"
    assert config.preset_alias == "cks"
    assert config.model["served_model_name"] == "Qwen/Qwen3-0.6B"
    assert config.namespace == "forge-llm-d"
    assert config.namespace_is_managed is False


def test_load_run_configuration_consolidates_config_d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, artifact_dir = _load_runtime_configuration(tmp_path)
    consolidated = llmd_runtime.load_yaml(artifact_dir / "config.yaml")

    assert "platform" in consolidated
    assert "model_cache" in consolidated
    assert "models" in consolidated
    assert "runtime" in consolidated
    assert "scheduler_profiles" in consolidated
    assert "vaults" in consolidated
    assert "caliper" in consolidated
    assert "workloads" in consolidated
    assert consolidated["project"]["name"] == "llm_d"
    assert consolidated["runtime"]["default_preset"] == "smoke"
    assert consolidated["vaults"] == ["psap-forge-notifications", "psap-forge-mlflow-export"]
    assert consolidated["caliper"]["export"]["from"] is None
    assert consolidated["caliper"]["export"]["backend"]["mlflow"]["enabled"] is True
    assert consolidated["platform"]["cluster"]["namespace"]["name"] == "forge-llm-d"
    assert isinstance(consolidated["platform"]["operators"], dict)


def test_namespace_override_is_not_managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _artifact_dir = _load_runtime_configuration(
        tmp_path,
        config_overrides={"platform.cluster.namespace.name": "custom-ns"},
    )

    assert config.namespace == "custom-ns"
    assert config.namespace_is_managed is False


def test_default_namespace_comes_from_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fournos_config.yaml").write_text(
        "job-name: llm-d-nightly\n",
        encoding="utf-8",
    )

    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    assert config.namespace == "forge-llm-d"
    assert config.namespace_is_managed is False
    assert config.platform["cluster"]["namespace"]["prefix"] == "llm-d"
    assert "rhods-operator" in config.platform["operators"]
    assert "rhcl-operator" in config.platform["operators"]


def test_load_run_configuration_ignores_runtime_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_PRESET", "benchmark-short")
    monkeypatch.setenv("FORGE_JOB_NAME", "ignored-job")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    llmd_configuration.prepare_runtime_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    config = llmd_runtime.load_run_configuration()

    assert config.preset_name == "smoke"
    assert config.namespace == "forge-llm-d"
    assert config.job_name == "local-smoke"


def test_runtime_overrides_win_over_preset_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(
        tmp_path,
        requested_preset="benchmark-short",
        config_overrides={
            "runtime.model_key": "qwen3-0-6b",
            "runtime.scheduler_profile_key": "default",
        },
    )

    assert config.preset_name == "benchmark-short"
    assert config.model_key == "qwen3-0-6b"
    assert config.scheduler_profile_key == "default"
    assert config.model["served_model_name"] == "Qwen/Qwen3-0.6B"
    assert config.benchmark["job_name"] == "guidellm-benchmark"


def test_write_prepare_inputs_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    path = phase_inputs.write_prepare_inputs(config)
    payload = llmd_runtime.load_yaml(path)
    loaded = phase_inputs.load_prepare_inputs(path)

    assert set(payload) == {
        "artifact_dir",
        "config_dir",
        "preset_name",
        "namespace",
        "namespace_is_managed",
        "platform",
        "model_key",
        "model",
        "model_cache",
        "benchmark",
    }
    assert loaded.artifact_dir == config.artifact_dir
    assert loaded.config_dir == config.config_dir
    assert loaded.namespace == config.namespace
    assert loaded.platform == config.platform
    assert loaded.model == config.model
    assert loaded.model_cache == config.model_cache
    assert loaded.benchmark == config.benchmark


def test_write_test_inputs_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    path = phase_inputs.write_test_inputs(config)
    payload = llmd_runtime.load_yaml(path)
    loaded = phase_inputs.load_test_inputs(path)

    assert set(payload) == {
        "artifact_dir",
        "config_dir",
        "preset_name",
        "namespace",
        "platform",
        "model_key",
        "model",
        "scheduler_profile_key",
        "scheduler_profile",
        "model_cache",
        "smoke_request",
        "benchmark",
    }
    assert loaded.namespace == config.namespace
    assert loaded.scheduler_profile_key == config.scheduler_profile_key
    assert loaded.smoke_request == config.smoke_request
    assert loaded.benchmark == config.benchmark


@pytest.mark.parametrize("orchestration", [llmd_ci, llmd_cli])
def test_orchestration_prepare_runs_sequence(
    orchestration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        orchestration,
        "load_runtime_configuration",
        lambda **_kwargs: config,
    )
    monkeypatch.setattr(
        orchestration,
        "run_prepare_sequence",
        lambda **kwargs: captured.update(kwargs) or 17,
    )

    result = orchestration.run_prepare_phase()

    assert result == 17
    assert captured == {
        "artifact_dir": config.artifact_dir,
        "config_dir": str(config.config_dir),
        "namespace": config.namespace,
        "namespace_is_managed": config.namespace_is_managed,
        "platform": config.platform,
        "model_key": config.model_key,
        "model": config.model,
        "model_cache": config.model_cache,
        "benchmark": config.benchmark,
    }


@pytest.mark.parametrize("orchestration", [llmd_ci, llmd_cli])
def test_orchestration_test_writes_inputs_and_invokes_toolbox(
    orchestration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        orchestration,
        "load_runtime_configuration",
        lambda **_kwargs: config,
    )
    monkeypatch.setattr(
        orchestration,
        "test_toolbox_run",
        lambda **kwargs: captured.update(kwargs) or 23,
    )

    result = orchestration.run_test_phase()

    assert result == 23
    assert captured == {
        "config_dir": str(config.config_dir),
        "namespace": config.namespace,
        "inference_service": config.platform["inference_service"],
        "gateway": config.platform["gateway"],
        "model_key": config.model_key,
        "model": config.model,
        "scheduler_profile_key": config.scheduler_profile_key,
        "scheduler_profile": config.scheduler_profile,
        "model_cache": config.model_cache,
        "smoke": config.platform["smoke"],
        "smoke_request": config.smoke_request,
        "benchmark": config.benchmark,
        "capture_namespace_events": config.platform["artifacts"]["capture_namespace_events"],
    }


@pytest.mark.parametrize("orchestration", [llmd_ci, llmd_cli])
def test_orchestration_cleanup_writes_inputs_and_invokes_toolbox(
    orchestration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        orchestration,
        "load_runtime_configuration",
        lambda **_kwargs: config,
    )
    monkeypatch.setattr(
        orchestration,
        "cleanup_toolbox_run",
        lambda **kwargs: captured.update(kwargs) or 29,
    )

    result = orchestration.run_cleanup_phase()

    assert result == 29
    assert captured == {
        "namespace": config.namespace,
        "inference_service_name": config.platform["inference_service"]["name"],
        "cleanup_timeout_seconds": config.platform["cluster"]["cleanup_timeout_seconds"],
        "benchmark_name": config.benchmark["job_name"] if config.benchmark else None,
    }


@pytest.mark.parametrize("orchestration", [llmd_ci, llmd_cli])
def test_orchestration_load_runtime_configuration_reads_env(
    orchestration, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_PRESET", "smoke-precise")
    monkeypatch.setenv("FORGE_JOB_NAME", "job-from-env")
    captured: dict[str, str | None] = {}
    sentinel = object()

    def fake_load_run_configuration(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        orchestration.llmd_configuration, "load_runtime_configuration", fake_load_run_configuration
    )

    result = orchestration.load_runtime_configuration()

    assert result is sentinel
    assert captured == {
        "requested_preset": "smoke-precise",
        "job_name": "job-from-env",
    }


def test_render_inference_service_injects_model_and_scheduler_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    manifest = llmd_runtime.render_inference_service(config)
    cache_spec = llmd_runtime.resolve_model_cache(config)

    assert manifest["metadata"]["name"] == "llm-d"
    assert manifest["metadata"]["namespace"] == config.namespace
    assert manifest["spec"]["model"]["name"] == "Qwen/Qwen3-0.6B"
    assert manifest["spec"]["model"]["uri"] == cache_spec.model_uri
    assert manifest["spec"]["model"]["name"] == config.model["served_model_name"]
    assert config.scheduler_profile_key == "approximate"
    router_args = manifest["spec"]["router"]["scheduler"]["template"]["containers"][0]["args"]
    assert router_args[-2] == "--config-text"
    assert "EndpointPickerConfig" in router_args[-1]
    assert "prefix-cache-scorer" in router_args[-1]


def test_render_inference_service_supports_precise_scheduler_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: smoke-precise\njob-name: llm-d-precise\n",
        encoding="utf-8",
    )

    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    manifest = llmd_runtime.render_inference_service(config)

    assert config.scheduler_profile_key == "precise"
    router_args = manifest["spec"]["router"]["scheduler"]["template"]["containers"][0]["args"]
    assert router_args[-2] == "--config-text"
    assert "precise-prefix-cache-scorer" in router_args[-1]
    assert "tokenizersCacheDir" in router_args[-1]


def test_render_inference_service_supports_default_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: smoke-default-scheduler\njob-name: llm-d-default\n",
        encoding="utf-8",
    )

    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    manifest = llmd_runtime.render_inference_service(config)

    assert config.scheduler_profile_key == "default"
    assert config.scheduler_profile is None
    assert manifest["spec"]["router"]["scheduler"] == {}


def test_resolve_model_cache_for_hf_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    cache_spec = llmd_runtime.resolve_model_cache(config)

    assert cache_spec is not None
    assert cache_spec.source_scheme == "hf"
    assert cache_spec.pvc_name.startswith("llm-d-model-qwen3-0-6b-")
    assert cache_spec.model_uri == f"pvc://{cache_spec.pvc_name}/model"
    assert cache_spec.pvc_size == "10Gi"
    assert cache_spec.access_mode == "ReadWriteOnce"


def test_render_model_cache_job_for_hf_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    cache_spec = llmd_runtime.resolve_model_cache(config)
    manifest = llmd_runtime.render_model_cache_job(config, cache_spec)

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "hf-model-downloader"
    assert container["image"] == "registry.access.redhat.com/ubi9/python-311"
    assert any(
        env["name"] == "MODEL_SOURCE" and env["value"] == "hf://Qwen/Qwen3-0.6B"
        for env in container["env"]
    )
    assert "huggingface_hub" in container["command"][-1]


def test_render_model_cache_job_for_oci_model_uses_registry_auth_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: benchmark-short\njob-name: llm-d-benchmark\n",
        encoding="utf-8",
    )

    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    monkeypatch.setattr(
        llmd_runtime,
        "resolve_default_serviceaccount_image_pull_secret",
        lambda namespace: "pull-secret",
    )
    cache_spec = llmd_runtime.resolve_model_cache(config)
    manifest = llmd_runtime.render_model_cache_job(config, cache_spec)

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    volume_names = {volume["name"] for volume in manifest["spec"]["template"]["spec"]["volumes"]}

    assert cache_spec.source_scheme == "oci"
    assert container["name"] == "oci-model-extractor"
    assert container["image"] == "registry.redhat.io/openshift4/ose-cli:v4.19"
    assert any(env["name"] == "OCI_IMAGE_PATH" and env["value"] == "/" for env in container["env"])
    assert "registry-auth" in volume_names
    assert "oc image extract" in container["command"][-1]


def test_render_guidellm_job_uses_target_and_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: benchmark-short\njob-name: llm-d-benchmark\n",
        encoding="utf-8",
    )

    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    manifest = llmd_runtime.render_guidellm_job(config, "https://example.test")

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "ghcr.io/vllm-project/guidellm:v0.5.4"
    assert "--target=https://example.test" in container["args"]
    assert "--rate=1" in container["args"]


def test_render_guidellm_job_supports_rate_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(
        tmp_path,
        requested_preset="benchmark-short",
    )
    benchmark = llmd_runtime.load_yaml(config.config_dir / "config.d" / "workloads.yaml")[
        "benchmarks"
    ]["concurrent-1k-1k"]
    config = replace(config, benchmark=benchmark)

    manifest = llmd_runtime.render_guidellm_job(config, "https://example.test")

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "--target=https://example.test" in container["args"]
    assert "--rate=1" not in container["args"]
    assert "--rates=300,200,100,50,1" in container["args"]
    assert "--max-seconds=600" in container["args"]
    assert "--data=prompt_tokens=1000,output_tokens=1000" in container["args"]


def test_render_smoke_request_job_uses_curl_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    payload = {"model": "Qwen/Qwen3-0.6B", "prompt": "test"}
    manifest = llmd_runtime.render_smoke_request_job(config, "https://example.test", payload)

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"]}

    assert manifest["kind"] == "Job"
    assert manifest["metadata"]["name"] == "llm-d-smoke"
    assert container["image"] == "curlimages/curl:8.11.1"
    assert env["ENDPOINT_URL"] == "https://example.test"
    assert env["ENDPOINT_PATH"] == "/v1/completions"
    assert env["REQUEST_PAYLOAD"] == '{"model": "Qwen/Qwen3-0.6B", "prompt": "test"}'


def test_prepare_model_cache_skips_ready_pvc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        prepare_model_cache_toolbox,
        "ensure_model_cache_pvc",
        lambda _config, _cache_spec: calls.append("ensure-pvc"),
    )
    monkeypatch.setattr(llmd_runtime, "model_cache_pvc_ready", lambda _cache_spec: True)
    monkeypatch.setattr(
        prepare_model_cache_toolbox,
        "capture_model_cache_state",
        lambda _config, _cache_spec: calls.append("capture"),
    )
    monkeypatch.setattr(
        prepare_model_cache_toolbox,
        "run_model_cache_download_job",
        lambda _config, _cache_spec: calls.append("download"),
    )

    prepare_model_cache_toolbox.run_prepare_model_cache(config)

    assert calls == ["ensure-pvc", "capture"]


def test_ensure_model_cache_pvc_does_not_wait_for_new_pvc_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    cache_spec = llmd_runtime.resolve_model_cache(config)
    calls: list[str] = []

    monkeypatch.setattr(llmd_runtime, "oc_get_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        llmd_runtime,
        "apply_manifest",
        lambda *args, **kwargs: calls.append("apply"),
    )
    monkeypatch.setattr(
        llmd_runtime,
        "wait_for_pvc_bound",
        lambda *args, **kwargs: pytest.fail("PVC binding should wait for a consumer"),
    )

    prepare_model_cache_toolbox.ensure_model_cache_pvc(config, cache_spec)

    assert calls == ["apply"]


def test_ensure_model_cache_pvc_accepts_existing_pending_pvc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    cache_spec = llmd_runtime.resolve_model_cache(config)

    monkeypatch.setattr(
        llmd_runtime,
        "oc_get_json",
        lambda *args, **kwargs: {
            "spec": {
                "accessModes": [cache_spec.access_mode],
                "storageClassName": cache_spec.storage_class_name,
            },
            "status": {"phase": "Pending"},
        },
    )
    monkeypatch.setattr(
        llmd_runtime,
        "wait_for_pvc_bound",
        lambda *args, **kwargs: pytest.fail("PVC binding should wait for a consumer"),
    )

    prepare_model_cache_toolbox.ensure_model_cache_pvc(config, cache_spec)


def test_prepare_model_cache_task_delegates_to_toolbox_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        prepare_toolbox,
        "prepare_model_cache_toolbox_run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.prepare_model_cache(
        namespace=config.namespace,
        namespace_is_managed=config.namespace_is_managed,
        model_key=config.model_key,
        model=config.model,
        model_cache=config.model_cache,
    )

    assert captured == {
        "namespace": config.namespace,
        "namespace_is_managed": config.namespace_is_managed,
        "model_key": config.model_key,
        "model": config.model,
        "model_cache": config.model_cache,
    }


def test_cleanup_deletes_leftovers_but_not_namespace_or_preserved_pvcs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    oc_calls: list[tuple[object, ...]] = []

    def fake_resource_exists(kind: str, name: str, namespace: str | None = None) -> bool:
        if kind == "namespace":
            return True
        return False

    monkeypatch.setattr(llmd_runtime, "resource_exists", fake_resource_exists)
    monkeypatch.setattr(
        llmd_runtime,
        "oc",
        lambda *args, **kwargs: (
            oc_calls.append((*args, kwargs.get("timeout_seconds")))  # type: ignore[misc]
            or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(llmd_runtime, "wait_until", lambda *args, **kwargs: True)
    monkeypatch.setattr(cleanup_toolbox, "_llm_d_pods_gone", lambda *_args: True)

    cleanup_toolbox.cleanup_namespace(
        namespace=config.namespace,
        inference_service_name=config.platform["inference_service"]["name"],
        cleanup_timeout_seconds=config.platform["cluster"]["cleanup_timeout_seconds"],
        benchmark_name=config.benchmark["job_name"] if config.benchmark else None,
    )

    assert (
        "delete",
        "pvc",
        "-n",
        config.namespace,
        "-l",
        "forge.openshift.io/project=llm_d,forge.openshift.io/preserve!=true",
        "--ignore-not-found=true",
        60,
    ) in oc_calls


def test_cleanup_previous_run_task_delegates_to_cleanup_toolbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        prepare_toolbox,
        "cleanup_toolbox_run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.cleanup_previous_run(
        namespace=config.namespace,
        inference_service_name=config.platform["inference_service"]["name"],
        cleanup_timeout_seconds=config.platform["cluster"]["cleanup_timeout_seconds"],
        benchmark_name=config.benchmark["job_name"] if config.benchmark else None,
    )
    assert captured == {
        "namespace": config.namespace,
        "inference_service_name": config.platform["inference_service"]["name"],
        "cleanup_timeout_seconds": config.platform["cluster"]["cleanup_timeout_seconds"],
        "benchmark_name": config.benchmark["job_name"] if config.benchmark else None,
    }


def test_prepare_gpu_operator_delegates_to_bootstrap_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    calls: list[str] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        prepare_toolbox,
        "ensure_operator_subscription",
        lambda operator_spec: calls.append(f"subscription:{operator_spec['package']}"),
    )
    monkeypatch.setattr(
        llmd_runtime,
        "wait_for_crd",
        lambda crd_name, *, timeout_seconds: calls.append(f"crd:{crd_name}"),
    )
    monkeypatch.setattr(
        bootstrap_gpu_clusterpolicy,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.prepare_gpu_operator(platform=config.platform)

    assert calls == [
        "subscription:gpu-operator-certified",
        "crd:clusterpolicies.nvidia.com",
    ]
    assert captured == {"timeout_seconds": 1800}


def test_prepare_nfd_delegates_to_bootstrap_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    calls: list[str] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        prepare_toolbox,
        "ensure_operator_subscription",
        lambda operator_spec: calls.append(f"subscription:{operator_spec['package']}"),
    )
    monkeypatch.setattr(
        llmd_runtime,
        "wait_for_crd",
        lambda crd_name, *, timeout_seconds: calls.append(f"crd:{crd_name}"),
    )
    monkeypatch.setattr(
        bootstrap_nfd_instance,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.prepare_nfd(platform=config.platform)

    assert calls == [
        "subscription:nfd",
        "crd:nodefeaturediscoveries.nfd.openshift.io",
    ]
    assert captured == {
        "gpu_label_selectors": ",".join(config.platform["cluster"]["nfd_gpu_detection_labels"]),
        "timeout_seconds": 900,
    }


def test_apply_datasciencecluster_delegates_to_toolbox_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        apply_datasciencecluster_toolbox,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.apply_datasciencecluster(
        config_dir=str(config.config_dir),
        rhoai=config.platform["rhoai"],
    )

    assert captured == {
        "config_dir": str(config.config_dir),
        "rhoai": config.platform["rhoai"],
    }


def test_wait_for_datasciencecluster_ready_delegates_to_toolbox_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        wait_datasciencecluster_ready_toolbox,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.wait_for_datasciencecluster_ready(rhoai=config.platform["rhoai"])

    assert captured == {"rhoai": config.platform["rhoai"]}


def test_ensure_gateway_delegates_to_toolbox_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ensure_gateway_toolbox,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.ensure_gateway(
        config_dir=str(config.config_dir),
        gateway=config.platform["gateway"],
    )

    assert captured == {
        "config_dir": str(config.config_dir),
        "gateway": config.platform["gateway"],
    }


def test_ensure_operator_subscription_delegates_to_cluster_toolbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    operator_spec = llmd_runtime.operator_spec_by_package(
        config.platform,
        "gpu-operator-certified",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cluster_deploy_operator,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    prepare_toolbox.ensure_operator_subscription(operator_spec)

    assert captured == {
        "package_name": "gpu-operator-certified",
        "target_namespace": "nvidia-gpu-operator",
        "source_name": "certified-operators",
        "channel": "stable",
        "source_namespace": "openshift-marketplace",
        "wait_timeout_seconds": 1800,
        "display_name": "NVIDIA GPU Operator",
    }


def test_ensure_global_operator_subscription_uses_global_operatorgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "rhcl-operator")
    subscription = llmd_runtime.desired_subscription(operator_spec)
    captured: dict[str, object] = {"oc_calls": []}

    monkeypatch.setattr(
        llmd_runtime,
        "ensure_namespace",
        lambda namespace: captured.setdefault("namespace", namespace),
    )

    def fake_oc_get_json(kind: str, **kwargs):
        captured.setdefault("get_calls", []).append((kind, kwargs))
        if kind == "operatorgroup":
            return {"items": [{"metadata": {"name": "global-operators"}, "spec": {}}]}
        if kind == "subscription.operators.coreos.com":
            return {"spec": subscription["spec"]}
        raise AssertionError(f"unexpected kind: {kind}")

    def fake_oc(*args, **kwargs):
        captured["oc_calls"].append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def fake_wait_until(*_args, predicate, **_kwargs):
        return predicate()

    monkeypatch.setattr(llmd_runtime, "oc_get_json", fake_oc_get_json)
    monkeypatch.setattr(llmd_runtime, "oc", fake_oc)
    monkeypatch.setattr(llmd_runtime, "wait_until", fake_wait_until)
    monkeypatch.setattr(
        llmd_runtime,
        "wait_for_operator_csv",
        lambda package, namespace, timeout_seconds: captured.setdefault(
            "csv",
            (package, namespace, timeout_seconds),
        ),
    )

    prepare_toolbox.ensure_global_operator_subscription(operator_spec)

    assert captured["namespace"] == "openshift-operators"
    assert captured["oc_calls"][0][0] == ("apply", "-f", "-")
    assert captured["csv"] == ("rhcl-operator", "openshift-operators", 1800)


def test_ensure_global_operator_subscription_requires_global_operatorgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "rhcl-operator")

    monkeypatch.setattr(llmd_runtime, "ensure_namespace", lambda _namespace: None)
    monkeypatch.setattr(
        llmd_runtime,
        "oc_get_json",
        lambda *_args, **_kwargs: {
            "items": [{"metadata": {"name": "single-ns"}, "spec": {"targetNamespaces": ["x"]}}]
        },
    )

    with pytest.raises(RuntimeError, match="requires a global OperatorGroup"):
        prepare_toolbox.ensure_global_operator_subscription(operator_spec)


def test_rhoai_custom_catalog_deploy_is_skipped_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    monkeypatch.setattr(
        deploy_custom_catalog,
        "run",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not deploy custom catalog")),
    )

    assert prepare_toolbox.deploy_rhoai_custom_catalog(rhoai=config.platform["rhoai"]) == 0


def test_rhoai_custom_catalog_deploys_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(
        tmp_path,
        config_overrides={
            "platform.rhoai.custom_catalog.enabled": True,
            "platform.rhoai.custom_catalog.name": "rhods-rc",
            "platform.rhoai.custom_catalog.namespace": "openshift-marketplace",
            "platform.rhoai.custom_catalog.image": "quay.io/example/rhoai-index:rc",
            "platform.rhoai.custom_catalog.display_name": "RHOAI RC Catalog",
            "platform.rhoai.custom_catalog.wait_timeout_seconds": 600,
        },
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        deploy_custom_catalog,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    assert prepare_toolbox.deploy_rhoai_custom_catalog(rhoai=config.platform["rhoai"]) == 0
    assert captured == {
        "catalog_source_name": "rhods-rc",
        "catalog_namespace": "openshift-marketplace",
        "catalog_image": "quay.io/example/rhoai-index:rc",
        "display_name": "RHOAI RC Catalog",
        "wait_timeout_seconds": 600,
    }


def test_rhoai_operator_spec_uses_custom_catalog_when_enabled(
    tmp_path: Path,
) -> None:
    config, _artifact_dir = _load_runtime_configuration(
        tmp_path,
        config_overrides={
            "platform.rhoai.custom_catalog.enabled": True,
            "platform.rhoai.custom_catalog.name": "rhods-rc",
            "platform.rhoai.custom_catalog.namespace": "openshift-marketplace",
            "platform.rhoai.custom_catalog.image": "quay.io/example/rhoai-index:rc",
        },
    )
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "rhods-operator")

    resolved = prepare_toolbox.rhoai_operator_spec(
        rhoai=config.platform["rhoai"],
        operator_spec=operator_spec,
    )

    assert resolved["source"] == "rhods-rc"
    assert resolved["source_namespace"] == "openshift-marketplace"


def test_gpu_clusterpolicy_manifest_has_required_default_sections() -> None:
    manifest = llmd_runtime.load_yaml(
        llmd_runtime.CONFIG_DIR / "manifests" / "gpu-clusterpolicy.yaml"
    )

    assert manifest["kind"] == "ClusterPolicy"
    assert manifest["metadata"]["name"] == "gpu-cluster-policy"
    assert {
        "daemonsets",
        "dcgm",
        "dcgmExporter",
        "devicePlugin",
        "driver",
        "gfd",
        "nodeStatusExporter",
        "operator",
        "toolkit",
    } <= set(manifest["spec"])


def test_resolve_endpoint_url_requires_gateway_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    def fake_oc_get_json(kind: str, **_: object) -> dict[str, object]:
        assert kind == "llminferenceservice"
        return {"status": {"addresses": [{"name": "other", "url": "https://wrong"}]}}

    monkeypatch.setattr(llmd_runtime, "oc_get_json", fake_oc_get_json)

    with pytest.raises(RuntimeError, match="Gateway address"):
        deploy_llmisvc_toolbox.resolve_endpoint_url(
            namespace=config.namespace,
            inference_service=config.platform["inference_service"],
            gateway=config.platform["gateway"],
        )


def test_run_smoke_request_uses_helper_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, artifact_dir = _load_runtime_configuration(tmp_path)
    oc_calls: list[tuple[str, ...]] = []
    applied: list[Path] = []

    def fake_oc(*args, **kwargs):
        oc_calls.append(tuple(args))
        if args[:2] == ("logs", "job/llm-d-smoke"):
            return subprocess.CompletedProcess(
                args,
                0,
                stdout='{"choices":[{"text":"ok"}]}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(llmd_runtime, "oc", fake_oc)
    monkeypatch.setattr(llmd_runtime, "resource_exists", lambda *args, **kwargs: False)
    monkeypatch.setattr(llmd_runtime, "wait_until", lambda *args, **kwargs: True)
    monkeypatch.setattr(llmd_runtime, "wait_for_job_completion", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        llmd_runtime,
        "apply_manifest",
        lambda artifact_path, _manifest: applied.append(artifact_path),
    )
    monkeypatch.setattr(run_smoke_request_toolbox, "capture_smoke_state", lambda **_kwargs: None)

    response = run_smoke_request_toolbox.run_smoke_request(
        artifact_dir=artifact_dir,
        namespace=config.namespace,
        smoke=config.platform["smoke"],
        model=config.model,
        smoke_request=config.smoke_request,
        endpoint_url="https://example.test",
    )

    assert response["choices"][0]["text"] == "ok"
    assert applied == [artifact_dir / "src" / "smoke-job.yaml"]
    assert not any(call and call[0] == "exec" for call in oc_calls)


def test_guidellm_toolbox_runs_benchmark_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved_config, artifact_dir = _load_runtime_configuration(
        tmp_path,
        requested_preset="benchmark-short",
    )
    config = phase_inputs.load_test_inputs(phase_inputs.write_test_inputs(resolved_config))
    benchmark_name = config.benchmark["job_name"]
    oc_calls: list[tuple[str, ...]] = []
    applied: list[Path] = []

    def fake_oc(*args, **kwargs):
        oc_calls.append(tuple(args))
        if args[:1] == ("exec",):
            return subprocess.CompletedProcess(args, 0, stdout='{"score": 1}\n', stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="state\n", stderr="")

    def fake_oc_get_json(kind: str, **_: object) -> dict[str, object]:
        if kind == "job":
            return {"status": {"succeeded": 1}}
        if kind == "pods":
            return {"items": [{"spec": {"nodeName": "worker-0"}}]}
        if kind == "pod":
            return {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        raise AssertionError(f"unexpected kind: {kind}")

    def fake_wait_until(*_args, predicate, **_kwargs):
        return predicate()

    monkeypatch.setattr(llmd_runtime, "oc", fake_oc)
    monkeypatch.setattr(llmd_runtime, "oc_get_json", fake_oc_get_json)
    monkeypatch.setattr(llmd_runtime, "wait_until", fake_wait_until)
    monkeypatch.setattr(
        llmd_runtime,
        "apply_manifest",
        lambda artifact_path, _manifest: applied.append(artifact_path),
    )

    args = SimpleNamespace(
        artifact_dir=config.artifact_dir,
        endpoint_url="https://example.test",
        namespace=config.namespace,
        benchmark=config.benchmark,
    )
    ctx = SimpleNamespace()

    run_guidellm_benchmark_toolbox.cleanup_previous_guidellm_resources_task(args, ctx)
    run_guidellm_benchmark_toolbox.create_guidellm_resources_task(args, ctx)
    run_guidellm_benchmark_toolbox.wait_guidellm_benchmark_task(args, ctx)
    run_guidellm_benchmark_toolbox.capture_guidellm_state_task(args, ctx)
    run_guidellm_benchmark_toolbox.copy_guidellm_results_task(args, ctx)

    assert applied == [
        artifact_dir / "src" / "guidellm-pvc.yaml",
        artifact_dir / "src" / "guidellm-job.yaml",
        artifact_dir / "src" / "guidellm-copy-pod.yaml",
    ]
    assert (
        "delete",
        "job,pvc",
        benchmark_name,
        "-n",
        config.namespace,
        "--ignore-not-found=true",
    ) in oc_calls
    assert (
        "exec",
        "-n",
        config.namespace,
        f"{benchmark_name}-copy",
        "--",
        "cat",
        "/results/benchmarks.json",
    ) in oc_calls
    assert (artifact_dir / "artifacts" / "results" / "benchmarks.json").read_text(
        encoding="utf-8"
    ) == '{"score": 1}\n'
    assert (artifact_dir / "artifacts" / "guidellm_benchmark_job.logs").read_text(
        encoding="utf-8"
    ) == "state\n"


def test_guidellm_cleanup_ignores_delete_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved_config, artifact_dir = _load_runtime_configuration(
        tmp_path,
        requested_preset="benchmark-short",
    )
    config = phase_inputs.load_test_inputs(phase_inputs.write_test_inputs(resolved_config))

    def fake_oc(*args, **kwargs):
        if args[:2] == ("delete", "job,pvc"):
            raise subprocess.TimeoutExpired(["oc", *args], timeout=60)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(llmd_runtime, "oc", fake_oc)

    args = SimpleNamespace(
        artifact_dir=artifact_dir,
        endpoint_url="https://example.test",
        namespace=config.namespace,
        benchmark=config.benchmark,
    )
    ctx = SimpleNamespace()

    result = run_guidellm_benchmark_toolbox.cleanup_previous_guidellm_resources_task(args, ctx)

    assert result == f"Deleted previous GuideLLM resources for {config.benchmark['job_name']}"


def test_test_phase_deploy_delegates_to_toolbox_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        deploy_llmisvc_toolbox,
        "run",
        lambda **kwargs: captured.update(kwargs) or "https://example.test",
    )

    result = test_toolbox.deploy_inference_service(
        config_dir=str(config.config_dir),
        namespace=config.namespace,
        inference_service=config.platform["inference_service"],
        gateway=config.platform["gateway"],
        model_key=config.model_key,
        model=config.model,
        scheduler_profile_key=config.scheduler_profile_key,
        scheduler_profile=config.scheduler_profile,
        model_cache=config.model_cache,
    )

    assert result == "https://example.test"
    assert captured == {
        "config_dir": str(config.config_dir),
        "namespace": config.namespace,
        "inference_service": config.platform["inference_service"],
        "gateway": config.platform["gateway"],
        "model_key": config.model_key,
        "model": config.model,
        "scheduler_profile_key": config.scheduler_profile_key,
        "scheduler_profile": config.scheduler_profile,
        "model_cache": config.model_cache,
    }


def test_test_phase_smoke_delegates_to_toolbox_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        run_smoke_request_toolbox,
        "run",
        lambda **kwargs: captured.update(kwargs) or {"choices": [{"text": "ok"}]},
    )

    response = test_toolbox.run_smoke_request(
        namespace=config.namespace,
        smoke=config.platform["smoke"],
        model=config.model,
        smoke_request=config.smoke_request,
        endpoint_url="https://example.test",
    )

    assert response["choices"][0]["text"] == "ok"
    assert captured == {
        "namespace": config.namespace,
        "smoke": config.platform["smoke"],
        "model": config.model,
        "smoke_request": config.smoke_request,
        "endpoint_url": "https://example.test",
    }


def test_test_phase_guidellm_delegates_to_toolbox_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(
        tmp_path,
        requested_preset="benchmark-short",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        run_guidellm_benchmark_toolbox,
        "run",
        lambda **kwargs: captured.update(kwargs) or 0,
    )

    result = test_toolbox.run_guidellm_benchmark(
        namespace=config.namespace,
        benchmark=config.benchmark,
        endpoint_url="https://example.test",
    )

    assert result is None
    assert captured == {
        "namespace": config.namespace,
        "benchmark": config.benchmark,
        "endpoint_url": "https://example.test",
    }


def test_test_phase_cleanup_deletes_helpers_and_llmisvc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(
        tmp_path,
        requested_preset="benchmark-short",
    )
    oc_calls: list[tuple[object, ...]] = []
    wait_descriptions: list[str] = []

    monkeypatch.setattr(
        llmd_runtime,
        "oc",
        lambda *args, **kwargs: (
            oc_calls.append((*args, kwargs.get("timeout_seconds")))  # type: ignore[misc]
            or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(llmd_runtime, "resource_exists", lambda *args, **kwargs: False)
    monkeypatch.setattr(llmd_runtime, "oc_get_json", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(
        llmd_runtime,
        "wait_until",
        lambda description, **kwargs: wait_descriptions.append(description) or True,
    )

    test_toolbox.cleanup_runtime_resources(
        namespace=config.namespace,
        inference_service=config.platform["inference_service"],
        smoke=config.platform["smoke"],
        benchmark=config.benchmark,
    )
    assert (
        "delete",
        "job",
        config.platform["smoke"]["job_name"],
        "-n",
        config.namespace,
        "--ignore-not-found=true",
        60,
    ) in oc_calls
    assert (
        "delete",
        "job,pvc",
        config.benchmark["job_name"],
        "-n",
        config.namespace,
        "--ignore-not-found=true",
        60,
    ) in oc_calls
    assert (
        "delete",
        "llminferenceservice",
        config.platform["inference_service"]["name"],
        "-n",
        config.namespace,
        "--ignore-not-found=true",
        60,
    ) in oc_calls
    assert any("llminferenceservice/" in description for description in wait_descriptions)
    assert any("workload pods deletion" in description for description in wait_descriptions)


def test_test_phase_cleanup_ignores_helper_delete_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _artifact_dir = _load_runtime_configuration(tmp_path)

    def fake_oc(*args, **kwargs):
        if args[:2] == ("delete", "job,pvc"):
            raise subprocess.TimeoutExpired(["oc", *args], timeout=60)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(llmd_runtime, "oc", fake_oc)
    monkeypatch.setattr(llmd_runtime, "resource_exists", lambda *args, **kwargs: False)
    monkeypatch.setattr(llmd_runtime, "oc_get_json", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(llmd_runtime, "wait_until", lambda *args, **kwargs: True)

    test_toolbox.cleanup_runtime_resources(
        namespace=config.namespace,
        inference_service=config.platform["inference_service"],
        smoke=config.platform["smoke"],
        benchmark=config.benchmark,
    )


def test_wait_until_reraises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="terminal failure"):
        llmd_runtime.wait_until(
            "test condition",
            timeout_seconds=1,
            interval_seconds=0,
            predicate=lambda: (_ for _ in ()).throw(RuntimeError("terminal failure")),
        )


def test_oc_forwards_timeout_to_run_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(llmd_runtime, "run_command", fake_run_command)

    llmd_runtime.oc("get", "pods", timeout_seconds=42)

    assert captured["args"] == ["oc", "get", "pods"]
    assert captured["kwargs"]["timeout_seconds"] == 42


def test_oc_get_json_returns_none_only_for_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llmd_runtime,
        "oc",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr='Error from server (NotFound): llminferenceservices.serving.kserve.io "llm-d" not found',
        ),
    )

    payload = llmd_runtime.oc_get_json(
        "llminferenceservice",
        name="llm-d",
        namespace="forge-llm-d",
        ignore_not_found=True,
    )

    assert payload is None


def test_oc_get_json_raises_for_non_not_found_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llmd_runtime,
        "oc",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr='Error from server (Forbidden): pods is forbidden: User "alice" cannot list resource "pods"',
        ),
    )

    with pytest.raises(llmd_runtime.CommandError, match="Forbidden"):
        llmd_runtime.oc_get_json("pods", namespace="forge-llm-d", ignore_not_found=True)


def test_resource_exists_propagates_non_not_found_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llmd_runtime,
        "oc_get_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(llmd_runtime.CommandError("boom")),
    )

    with pytest.raises(llmd_runtime.CommandError, match="boom"):
        llmd_runtime.resource_exists("namespace", "forge-llm-d")
