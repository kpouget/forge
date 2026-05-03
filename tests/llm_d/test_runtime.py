from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from projects.llm_d.orchestration import ci as llmd_ci
from projects.llm_d.orchestration import cli as llmd_cli
from projects.llm_d.orchestration import llmd_runtime, phase_inputs
from projects.llm_d.toolbox.cleanup import main as cleanup_toolbox
from projects.llm_d.toolbox.prepare import main as prepare_toolbox
from projects.llm_d.toolbox.prepare_model_cache import main as prepare_model_cache_toolbox
from projects.llm_d.toolbox.test import main as test_toolbox


def test_derive_namespace_uses_prefix_once() -> None:
    namespace = llmd_runtime.derive_namespace("llm-d-nightly-smoke", "llm-d", 63)
    assert namespace == "llm-d-nightly-smoke"


def test_parse_overrides_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="Unsupported llm_d override keys"):
        llmd_runtime.parse_overrides('{"model":"other"}', allowed_keys=("namespace",))


def test_load_run_configuration_resolves_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    fournos_config = tmp_path / "fournos_config.yaml"
    fournos_config.write_text(
        "preset: cks\njob-name: llm-d-e2e\n",
        encoding="utf-8",
    )

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

    assert config.preset_name == "smoke"
    assert config.preset_alias == "cks"
    assert config.model["served_model_name"] == "Qwen/Qwen3-0.6B"
    assert config.namespace == "forge-llm-d"
    assert config.namespace_is_managed is False


def test_load_run_configuration_consolidates_config_d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    consolidated = llmd_runtime.load_yaml(artifact_dir / "config.yaml")

    assert "platform" in consolidated
    assert "model_cache" in consolidated
    assert "models" in consolidated
    assert "runtime" in consolidated
    assert "scheduler_profiles" in consolidated
    assert "workloads" in consolidated
    assert consolidated["runtime"]["default_preset"] == "smoke"


def test_namespace_override_is_not_managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", '{"namespace":"custom-ns"}')
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

    assert config.namespace == "custom-ns"
    assert config.namespace_is_managed is False


def test_default_namespace_comes_from_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (tmp_path / "fournos_config.yaml").write_text(
        "job-name: llm-d-nightly\n",
        encoding="utf-8",
    )

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

    assert config.namespace == "forge-llm-d"
    assert config.namespace_is_managed is False


def test_write_prepare_inputs_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

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
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

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
def test_orchestration_prepare_writes_inputs_and_invokes_toolbox(
    orchestration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    captured: dict[str, str] = {}

    monkeypatch.setattr(orchestration.llmd_runtime, "load_run_configuration", lambda: config)
    monkeypatch.setattr(
        orchestration,
        "prepare_toolbox_run",
        lambda *, inputs_file: captured.setdefault("inputs_file", inputs_file) or 17,
    )

    result = orchestration.run_prepare_phase()
    loaded = phase_inputs.load_prepare_inputs(captured["inputs_file"])

    assert result == captured["inputs_file"]
    assert loaded.namespace == config.namespace


@pytest.mark.parametrize("orchestration", [llmd_ci, llmd_cli])
def test_orchestration_test_writes_inputs_and_invokes_toolbox(
    orchestration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    captured: dict[str, str] = {}

    monkeypatch.setattr(orchestration.llmd_runtime, "load_run_configuration", lambda: config)
    monkeypatch.setattr(
        orchestration,
        "test_toolbox_run",
        lambda *, inputs_file: captured.setdefault("inputs_file", inputs_file) or 23,
    )

    result = orchestration.run_test_phase()
    loaded = phase_inputs.load_test_inputs(captured["inputs_file"])

    assert result == captured["inputs_file"]
    assert loaded.namespace == config.namespace
    assert loaded.model == config.model


@pytest.mark.parametrize("orchestration", [llmd_ci, llmd_cli])
def test_orchestration_cleanup_writes_inputs_and_invokes_toolbox(
    orchestration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    captured: dict[str, str] = {}

    monkeypatch.setattr(orchestration.llmd_runtime, "load_run_configuration", lambda: config)
    monkeypatch.setattr(
        orchestration,
        "cleanup_toolbox_run",
        lambda *, inputs_file: captured.setdefault("inputs_file", inputs_file) or 29,
    )

    result = orchestration.run_cleanup_phase()
    loaded = phase_inputs.load_cleanup_inputs(captured["inputs_file"])

    assert result == captured["inputs_file"]
    assert loaded.namespace == config.namespace
    assert loaded.platform == config.platform


def test_render_inference_service_injects_model_and_scheduler_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
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
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: smoke-precise\njob-name: llm-d-precise\n",
        encoding="utf-8",
    )

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    manifest = llmd_runtime.render_inference_service(config)

    assert config.scheduler_profile_key == "precise"
    router_args = manifest["spec"]["router"]["scheduler"]["template"]["containers"][0]["args"]
    assert router_args[-2] == "--config-text"
    assert "precise-prefix-cache-scorer" in router_args[-1]
    assert "tokenizersCacheDir" in router_args[-1]


def test_render_inference_service_supports_default_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: smoke-default-scheduler\njob-name: llm-d-default\n",
        encoding="utf-8",
    )

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    manifest = llmd_runtime.render_inference_service(config)

    assert config.scheduler_profile_key == "default"
    assert config.scheduler_profile is None
    assert manifest["spec"]["router"]["scheduler"] == {}


def test_resolve_model_cache_for_hf_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
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
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
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
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: benchmark-short\njob-name: llm-d-benchmark\n",
        encoding="utf-8",
    )

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
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
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (tmp_path / "fournos_config.yaml").write_text(
        "preset: benchmark-short\njob-name: llm-d-benchmark\n",
        encoding="utf-8",
    )

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    manifest = llmd_runtime.render_guidellm_job(config, "https://example.test")

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "ghcr.io/vllm-project/guidellm:v0.5.4"
    assert "--target=https://example.test" in container["args"]
    assert "--rate=1" in container["args"]


def test_render_smoke_request_job_uses_curl_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
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
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
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


def test_cleanup_deletes_leftovers_but_not_namespace_or_preserved_pvcs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    shell_calls: list[str] = []

    def fake_resource_exists(kind: str, name: str, namespace: str | None = None) -> bool:
        if kind == "namespace":
            return True
        return False

    monkeypatch.setattr(llmd_runtime, "resource_exists", fake_resource_exists)
    monkeypatch.setattr(
        cleanup_toolbox.shell,
        "run",
        lambda command, **kwargs: shell_calls.append(command),
    )
    monkeypatch.setattr(llmd_runtime, "wait_until", lambda *args, **kwargs: True)
    monkeypatch.setattr(cleanup_toolbox, "_llm_d_pods_gone", lambda *_args: True)

    cleanup_toolbox.delete_run_leftovers(config)

    assert f"oc delete namespace {config.namespace} --ignore-not-found=true" not in shell_calls
    assert (
        f"oc delete pvc -n {config.namespace} "
        '-l "forge.openshift.io/project=llm_d,forge.openshift.io/preserve!=true" '
        "--ignore-not-found=true"
    ) in shell_calls


def test_prepare_gpu_operator_skips_existing_clusterpolicy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

    calls: list[str] = []

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
        llmd_runtime,
        "load_manifest_template",
        lambda _config, _path: {
            "apiVersion": "nvidia.com/v1",
            "kind": "ClusterPolicy",
            "metadata": {"name": "gpu-cluster-policy"},
            "spec": {},
        },
    )
    monkeypatch.setattr(llmd_runtime, "resource_exists", lambda kind, name: True)

    def fail_apply(*_: object, **__: object) -> None:
        raise AssertionError("existing ClusterPolicy must not be reapplied")

    monkeypatch.setattr(llmd_runtime, "apply_manifest", fail_apply)
    monkeypatch.setattr(
        llmd_runtime,
        "oc_get_json",
        lambda kind, name: {"status": {"state": "ready"}},
    )

    prepare_toolbox.prepare_gpu_operator(config)

    assert calls == [
        "subscription:gpu-operator-certified",
        "crd:clusterpolicies.nvidia.com",
    ]


def test_prepare_gpu_operator_bootstraps_missing_clusterpolicy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

    applied: list[Path] = []
    manifest = {
        "apiVersion": "nvidia.com/v1",
        "kind": "ClusterPolicy",
        "metadata": {"name": "gpu-cluster-policy"},
        "spec": {},
    }

    monkeypatch.setattr(prepare_toolbox, "ensure_operator_subscription", lambda _: None)
    monkeypatch.setattr(llmd_runtime, "wait_for_crd", lambda *_, **__: None)
    monkeypatch.setattr(llmd_runtime, "load_manifest_template", lambda _config, _path: manifest)
    monkeypatch.setattr(llmd_runtime, "resource_exists", lambda kind, name: False)
    monkeypatch.setattr(
        llmd_runtime,
        "apply_manifest",
        lambda artifact_path, _manifest: applied.append(artifact_path),
    )
    monkeypatch.setattr(
        llmd_runtime,
        "oc_get_json",
        lambda kind, name: {"status": {"state": "ready"}},
    )

    prepare_toolbox.prepare_gpu_operator(config)

    assert applied == [artifact_dir / "src" / "gpu-clusterpolicy.yaml"]


def test_prepare_nfd_skips_existing_nodefeaturediscovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

    calls: list[str] = []
    manifest = {
        "apiVersion": "nfd.openshift.io/v1",
        "kind": "NodeFeatureDiscovery",
        "metadata": {"name": "nfd-instance", "namespace": "openshift-nfd"},
    }

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
    monkeypatch.setattr(llmd_runtime, "load_manifest_template", lambda _config, _path: manifest)
    monkeypatch.setattr(llmd_runtime, "resource_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        llmd_runtime,
        "wait_until",
        lambda *args, **kwargs: calls.append("wait-nfd"),
    )
    monkeypatch.setattr(
        prepare_toolbox,
        "wait_for_nfd_gpu_labels",
        lambda _config, *, timeout_seconds: calls.append("wait-labels"),
    )

    def fail_apply(*_: object, **__: object) -> None:
        raise AssertionError("existing NodeFeatureDiscovery must not be reapplied")

    monkeypatch.setattr(llmd_runtime, "apply_manifest", fail_apply)

    prepare_toolbox.prepare_nfd(config)

    assert calls == [
        "subscription:nfd",
        "crd:nodefeaturediscoveries.nfd.openshift.io",
        "wait-nfd",
        "wait-labels",
    ]


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
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)

    def fake_oc_get_json(kind: str, **_: object) -> dict[str, object]:
        assert kind == "llminferenceservice"
        return {"status": {"addresses": [{"name": "other", "url": "https://wrong"}]}}

    monkeypatch.setattr(llmd_runtime, "oc_get_json", fake_oc_get_json)

    with pytest.raises(RuntimeError, match="Gateway address"):
        test_toolbox.resolve_endpoint_url(config)


def test_run_smoke_request_uses_helper_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
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
    monkeypatch.setattr(test_toolbox, "capture_smoke_state", lambda _config: None)

    response = test_toolbox.run_smoke_request(config, "https://example.test")

    assert response["choices"][0]["text"] == "ok"
    assert applied == [artifact_dir / "src" / "smoke-job.yaml"]
    assert not any(call and call[0] == "exec" for call in oc_calls)


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
