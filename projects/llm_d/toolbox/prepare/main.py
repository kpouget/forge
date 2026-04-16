#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
from pathlib import Path

from projects.core.dsl import toolbox
from projects.llm_d.orchestration import llmd_runtime
from projects.llm_d.toolbox.cleanup import main as cleanup_toolbox
from projects.llm_d.toolbox.prepare_model_cache import main as prepare_model_cache

LOGGER = logging.getLogger(__name__)


def run() -> int:
    llmd_runtime.init()
    config = llmd_runtime.load_run_configuration()
    return run_prepare(config)


def run_prepare(config: llmd_runtime.ResolvedConfig) -> int:
    LOGGER.info("Preparing llm_d preset=%s namespace=%s", config.preset_name, config.namespace)

    verify_oc_access()
    verify_cluster_version(config)
    prepare_cert_manager(config)
    prepare_leader_worker_set(config)
    prepare_nfd(config)
    prepare_gpu_operator(config)
    prepare_rhoai_operator(config)
    apply_datasciencecluster(config)
    wait_for_datasciencecluster_ready(config)
    ensure_required_crds(config.platform["rhoai"]["required_crds_after_dsc"], config)
    ensure_gateway(config)
    ensure_test_namespace(config)
    cleanup_toolbox.delete_run_leftovers(config)
    prepare_model_cache.run_prepare_model_cache(config)
    verify_gpu_nodes(config)
    capture_prepare_state(config)

    return 0


def verify_oc_access() -> None:
    llmd_runtime.oc("whoami", capture_output=True)


def verify_cluster_version(config: llmd_runtime.ResolvedConfig) -> None:
    version_info = llmd_runtime.oc("version", "-o", "json", capture_output=True)
    payload = json.loads(version_info.stdout)

    openshift_version = (
        payload.get("openshiftVersion")
        or payload.get("serverVersion", {}).get("gitVersion")
        or payload.get("serverVersion", {}).get("platform")
    )
    if not openshift_version:
        raise RuntimeError("Could not determine OpenShift version from `oc version -o json`")

    minimum = config.platform["cluster"]["minimum_openshift_version"]
    if llmd_runtime.version_tuple(openshift_version) < llmd_runtime.version_tuple(minimum):
        raise RuntimeError(
            f"Cluster version {openshift_version} is older than the llm_d minimum {minimum}"
        )


def ensure_operator_subscription(operator_spec: dict[str, str]) -> dict[str, object]:
    llmd_runtime.ensure_subscription(operator_spec)
    return llmd_runtime.wait_for_operator_csv(
        operator_spec["package"],
        operator_spec["namespace"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )


def prepare_cert_manager(config: llmd_runtime.ResolvedConfig) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(
        config.platform, "openshift-cert-manager-operator"
    )
    ensure_operator_subscription(operator_spec)


def prepare_leader_worker_set(config: llmd_runtime.ResolvedConfig) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "leader-worker-set")
    ensure_operator_subscription(operator_spec)


def prepare_nfd(config: llmd_runtime.ResolvedConfig) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "nfd")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )

    manifest = llmd_runtime.load_manifest_template(config, operator_spec["bootstrap_manifest"])
    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "nfd-nodefeaturediscovery.yaml",
        manifest,
    )

    llmd_runtime.wait_until(
        "NodeFeatureDiscovery bootstrap resource",
        timeout_seconds=operator_spec["wait_timeout_seconds"],
        interval_seconds=10,
        predicate=lambda: llmd_runtime.resource_exists(
            "nodefeaturediscovery",
            manifest["metadata"]["name"],
            namespace=manifest["metadata"]["namespace"],
        ),
    )

    wait_for_nfd_gpu_labels(config, timeout_seconds=operator_spec["wait_timeout_seconds"])


def prepare_gpu_operator(config: llmd_runtime.ResolvedConfig) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "gpu-operator-certified")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )

    manifest = llmd_runtime.load_manifest_template(config, operator_spec["bootstrap_manifest"])
    clusterpolicy_name = manifest["metadata"]["name"]
    if llmd_runtime.resource_exists("clusterpolicy", clusterpolicy_name):
        LOGGER.info(
            "ClusterPolicy/%s already exists; verifying readiness instead of applying bootstrap manifest",
            clusterpolicy_name,
        )
        wait_for_gpu_clusterpolicy_ready(
            clusterpolicy_name,
            timeout_seconds=operator_spec["wait_timeout_seconds"],
        )
        return

    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "gpu-clusterpolicy.yaml",
        manifest,
    )

    wait_for_gpu_clusterpolicy_ready(
        clusterpolicy_name,
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )


def wait_for_gpu_clusterpolicy_ready(clusterpolicy_name: str, *, timeout_seconds: int) -> None:
    def _clusterpolicy_ready() -> bool:
        payload = llmd_runtime.oc_get_json(
            "clusterpolicy",
            name=clusterpolicy_name,
        )
        state = payload.get("status", {}).get("state", "")
        return state.lower() == "ready"

    llmd_runtime.wait_until(
        f"clusterpolicy/{clusterpolicy_name} ready",
        timeout_seconds=timeout_seconds,
        interval_seconds=15,
        predicate=_clusterpolicy_ready,
    )


def prepare_rhoai_operator(config: llmd_runtime.ResolvedConfig) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "rhods-operator")
    ensure_operator_subscription(operator_spec)
    ensure_required_crds(config.platform["rhoai"]["required_crds_before_dsc"], config)


def ensure_required_crds(crd_names: list[str], config: llmd_runtime.ResolvedConfig) -> None:
    for crd_name in crd_names:
        llmd_runtime.wait_for_crd(
            crd_name,
            timeout_seconds=config.platform["rhoai"]["wait_timeout_seconds"],
        )


def apply_datasciencecluster(config: llmd_runtime.ResolvedConfig) -> None:
    manifest = llmd_runtime.render_datasciencecluster(config)
    llmd_runtime.apply_manifest(config.artifact_dir / "src" / "datasciencecluster.yaml", manifest)
    llmd_runtime.oc(
        "get",
        "datasciencecluster",
        config.platform["rhoai"]["datasciencecluster_name"],
        "-n",
        config.platform["rhoai"]["namespace"],
        "-o",
        "yaml",
        capture_output=True,
    )


def wait_for_datasciencecluster_ready(config: llmd_runtime.ResolvedConfig) -> None:
    rhoai = config.platform["rhoai"]

    def _dsc_ready() -> bool:
        payload = llmd_runtime.oc_get_json(
            "datasciencecluster",
            name=rhoai["datasciencecluster_name"],
            namespace=rhoai["namespace"],
        )
        phase = payload.get("status", {}).get("phase")
        if phase == "Ready":
            return True
        if phase in {"Failed", "Error"}:
            raise RuntimeError(f"DataScienceCluster entered terminal phase {phase}")
        return False

    llmd_runtime.wait_until(
        f"datasciencecluster/{rhoai['datasciencecluster_name']} ready",
        timeout_seconds=rhoai["wait_timeout_seconds"],
        interval_seconds=10,
        predicate=_dsc_ready,
    )


def ensure_gateway(config: llmd_runtime.ResolvedConfig) -> None:
    gateway = config.platform["gateway"]
    if not llmd_runtime.resource_exists("gateway", gateway["name"], namespace=gateway["namespace"]):
        if not gateway["create_if_missing"]:
            raise RuntimeError(
                f"Required gateway {gateway['name']} does not exist in {gateway['namespace']}"
            )
        manifest = llmd_runtime.render_gateway(config)
        llmd_runtime.apply_manifest(config.artifact_dir / "src" / "gateway.yaml", manifest)

    def _gateway_programmed() -> bool:
        resource = llmd_runtime.oc_get_json(
            "gateway",
            name=gateway["name"],
            namespace=gateway["namespace"],
        )
        return llmd_runtime.condition_status(resource, "Programmed") == "True"

    llmd_runtime.wait_until(
        f"gateway/{gateway['name']} programmed",
        timeout_seconds=gateway["wait_timeout_seconds"],
        interval_seconds=10,
        predicate=_gateway_programmed,
    )


def ensure_test_namespace(config: llmd_runtime.ResolvedConfig) -> None:
    llmd_runtime.ensure_namespace(
        config.namespace,
        labels={
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        },
    )


def verify_gpu_nodes(config: llmd_runtime.ResolvedConfig) -> None:
    selector = config.platform["cluster"]["gpu_node_label_selector"]
    data = llmd_runtime.oc_get_json("nodes", selector=selector, ignore_not_found=True)
    items = data.get("items", []) if data else []
    if not items:
        raise RuntimeError(
            f"No GPU nodes found with selector {selector}. The llm_d smoke path requires GPUs."
        )


def wait_for_nfd_gpu_labels(config: llmd_runtime.ResolvedConfig, *, timeout_seconds: int) -> None:
    selectors = config.platform["cluster"]["nfd_gpu_detection_labels"]

    def _labels_present() -> bool:
        for selector in selectors:
            data = llmd_runtime.oc_get_json("nodes", selector=selector, ignore_not_found=True)
            if data and data.get("items"):
                return True
        return False

    llmd_runtime.wait_until(
        "NFD GPU discovery labels on cluster nodes",
        timeout_seconds=timeout_seconds,
        interval_seconds=15,
        predicate=_labels_present,
    )


def capture_prepare_state(config: llmd_runtime.ResolvedConfig) -> None:
    artifacts_dir = config.artifact_dir / "artifacts"
    rhoai = config.platform["rhoai"]
    gateway = config.platform["gateway"]

    capture_resource_yaml(
        "datasciencecluster",
        rhoai["datasciencecluster_name"],
        rhoai["namespace"],
        artifacts_dir / "datasciencecluster.yaml",
    )
    capture_resource_yaml(
        "gateway",
        gateway["name"],
        gateway["namespace"],
        artifacts_dir / "gateway.yaml",
    )
    gateway_service = llmd_runtime.oc(
        "get",
        "service",
        "-A",
        "-l",
        f"gateway.networking.k8s.io/gateway-name={gateway['name']}",
        "-o",
        "yaml",
        check=False,
        capture_output=True,
    )
    if gateway_service.returncode == 0 and gateway_service.stdout:
        llmd_runtime.write_text(artifacts_dir / "gateway.service.yaml", gateway_service.stdout)
    if config.platform["artifacts"]["capture_namespace_events"]:
        capture_namespace_events(config.namespace, artifacts_dir / "namespace.events.txt")


def capture_resource_yaml(
    kind: str,
    name: str,
    namespace: str,
    destination: Path,
    *,
    check: bool = True,
) -> None:
    result = llmd_runtime.oc(
        "get",
        kind,
        name,
        "-n",
        namespace,
        "-o",
        "yaml",
        check=check,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        llmd_runtime.write_text(destination, result.stdout)


def capture_namespace_events(namespace: str, destination: Path) -> None:
    result = llmd_runtime.oc(
        "get",
        "events",
        "-n",
        namespace,
        "--sort-by=.metadata.creationTimestamp",
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        llmd_runtime.write_text(destination, result.stdout)


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
