from __future__ import annotations

from pathlib import Path

import pytest

from projects.llm_d.orchestration import llmd_runtime
from projects.llm_d.toolbox.prepare import main as prepare_toolbox
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

    config = llmd_runtime.load_run_configuration(
        cwd=tmp_path, artifact_dir=artifact_dir
    )

    assert config.preset_name == "smoke"
    assert config.preset_alias == "cks"
    assert config.model["served_model_name"] == "Qwen/Qwen3-0.6B"
    assert config.namespace == "llm-d-e2e"
    assert config.namespace_is_managed is True


def test_load_run_configuration_consolidates_config_d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    llmd_runtime.load_run_configuration(cwd=tmp_path, artifact_dir=artifact_dir)
    consolidated = llmd_runtime.load_yaml(artifact_dir / "config.yaml")

    assert "platform" in consolidated
    assert "models" in consolidated
    assert "runtime" in consolidated
    assert "workloads" in consolidated
    assert consolidated["runtime"]["default_preset"] == "smoke"


def test_namespace_override_is_not_managed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", '{"namespace":"custom-ns"}')
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(
        cwd=tmp_path, artifact_dir=artifact_dir
    )

    assert config.namespace == "custom-ns"
    assert config.namespace_is_managed is False


def test_render_inference_service_injects_model_and_epp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    config = llmd_runtime.load_run_configuration(
        cwd=tmp_path, artifact_dir=artifact_dir
    )
    manifest = llmd_runtime.render_inference_service(config)

    assert manifest["metadata"]["name"] == "llm-d"
    assert manifest["metadata"]["namespace"] == config.namespace
    assert manifest["spec"]["model"]["name"] == "Qwen/Qwen3-0.6B"
    assert manifest["spec"]["model"]["uri"] == "hf://Qwen/Qwen3-0.6B"
    assert manifest["spec"]["model"]["name"] == config.model["served_model_name"]
    assert manifest["spec"]["model"]["uri"] == config.model["uri"]
    router_args = manifest["spec"]["router"]["scheduler"]["template"]["containers"][0][
        "args"
    ]
    assert router_args[-2] == "--config-text"
    assert "EndpointPickerConfig" in router_args[-1]


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

    config = llmd_runtime.load_run_configuration(
        cwd=tmp_path, artifact_dir=artifact_dir
    )
    manifest = llmd_runtime.render_guidellm_job(config, "https://example.test")

    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "ghcr.io/vllm-project/guidellm:v0.5.4"
    assert "--target=https://example.test" in container["args"]
    assert "--rate=1" in container["args"]


def test_prepare_gpu_operator_skips_existing_clusterpolicy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_CONFIG_OVERRIDES", "{}")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    config = llmd_runtime.load_run_configuration(
        cwd=tmp_path, artifact_dir=artifact_dir
    )

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
    config = llmd_runtime.load_run_configuration(
        cwd=tmp_path, artifact_dir=artifact_dir
    )

    applied: list[Path] = []
    manifest = {
        "apiVersion": "nvidia.com/v1",
        "kind": "ClusterPolicy",
        "metadata": {"name": "gpu-cluster-policy"},
        "spec": {},
    }

    monkeypatch.setattr(prepare_toolbox, "ensure_operator_subscription", lambda _: None)
    monkeypatch.setattr(llmd_runtime, "wait_for_crd", lambda *_, **__: None)
    monkeypatch.setattr(
        llmd_runtime, "load_manifest_template", lambda _config, _path: manifest
    )
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
    config = llmd_runtime.load_run_configuration(
        cwd=tmp_path, artifact_dir=artifact_dir
    )

    def fake_oc_get_json(kind: str, **_: object) -> dict[str, object]:
        assert kind == "llminferenceservice"
        return {"status": {"addresses": [{"name": "other", "url": "https://wrong"}]}}

    monkeypatch.setattr(llmd_runtime, "oc_get_json", fake_oc_get_json)

    with pytest.raises(RuntimeError, match="Gateway address"):
        test_toolbox.resolve_endpoint_url(config)
