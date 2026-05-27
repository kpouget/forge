from __future__ import annotations

import json
import logging
from pathlib import Path

from projects.cluster.toolbox.cluster_deploy_operator import main as cluster_deploy_operator
from projects.cluster.toolbox.deploy_custom_catalog import main as deploy_custom_catalog
from projects.llm_d.orchestration.cleanup_phase import run as cleanup_toolbox_run
from projects.llm_d.runtime import llmd_runtime
from projects.llm_d.toolbox.apply_datasciencecluster import main as apply_datasciencecluster_command
from projects.llm_d.toolbox.bootstrap_gpu_clusterpolicy import main as bootstrap_gpu_clusterpolicy
from projects.llm_d.toolbox.bootstrap_nfd_instance import main as bootstrap_nfd_instance
from projects.llm_d.toolbox.ensure_gateway import main as ensure_gateway_command
from projects.llm_d.toolbox.prepare_model_cache.main import run as prepare_model_cache_toolbox_run
from projects.llm_d.toolbox.wait_datasciencecluster_ready import (
    main as wait_datasciencecluster_ready_command,
)

LOGGER = logging.getLogger(__name__)


def verify_oc_access() -> None:
    llmd_runtime.oc("whoami", capture_output=True)


def verify_cluster_version(*, platform: dict) -> None:
    version_info = llmd_runtime.oc("version", "-o", "json", capture_output=True)
    payload = json.loads(version_info.stdout)

    openshift_version = (
        payload.get("openshiftVersion")
        or payload.get("serverVersion", {}).get("gitVersion")
        or payload.get("serverVersion", {}).get("platform")
    )
    if not openshift_version:
        raise RuntimeError("Could not determine OpenShift version from `oc version -o json`")

    minimum = platform["cluster"]["minimum_openshift_version"]
    if llmd_runtime.version_tuple(openshift_version) < llmd_runtime.version_tuple(minimum):
        raise RuntimeError(
            f"Cluster version {openshift_version} is older than the llm_d minimum {minimum}"
        )


def ensure_operator_subscription(operator_spec: dict[str, str]) -> dict[str, object]:
    return cluster_deploy_operator.run(
        package_name=operator_spec["package"],
        target_namespace=operator_spec["namespace"],
        source_name=operator_spec["source"],
        channel=operator_spec["channel"],
        source_namespace=operator_spec.get("source_namespace", "openshift-marketplace"),
        wait_timeout_seconds=operator_spec["wait_timeout_seconds"],
        display_name=operator_spec.get("display_name", operator_spec["package"]),
    )


def deploy_rhoai_custom_catalog(*, rhoai: dict) -> int:
    custom_catalog = rhoai["custom_catalog"]
    if not custom_catalog["enabled"]:
        LOGGER.info("RHOAI custom catalog disabled; using default catalog source")
        return 0

    if not custom_catalog.get("image"):
        raise RuntimeError("RHOAI custom catalog is enabled but no image was configured")

    return deploy_custom_catalog.run(
        catalog_source_name=custom_catalog["name"],
        catalog_namespace=custom_catalog["namespace"],
        catalog_image=custom_catalog["image"],
        display_name=custom_catalog.get("display_name", custom_catalog["name"]),
        wait_timeout_seconds=custom_catalog["wait_timeout_seconds"],
    )


def rhoai_operator_spec(
    *,
    rhoai: dict,
    operator_spec: dict[str, str],
) -> dict[str, str]:
    custom_catalog = rhoai["custom_catalog"]
    if not custom_catalog["enabled"]:
        return operator_spec

    updated_spec = dict(operator_spec)
    updated_spec["source"] = custom_catalog["name"]
    updated_spec["source_namespace"] = custom_catalog["namespace"]
    return updated_spec


def prepare_rhcl_operator(*, platform: dict) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(platform, "rhcl-operator")
    ensure_global_operator_subscription(operator_spec)


def ensure_global_operator_subscription(operator_spec: dict[str, str]) -> None:
    namespace = operator_spec["namespace"]
    package = operator_spec["package"]

    llmd_runtime.ensure_namespace(namespace)
    operator_groups = llmd_runtime.oc_get_json(
        "operatorgroup",
        namespace=namespace,
        ignore_not_found=True,
    )
    if not any(
        not item.get("spec", {}).get("targetNamespaces")
        for item in (operator_groups or {}).get("items", [])
    ):
        raise RuntimeError(
            f"Operator {package} requires a global OperatorGroup in {namespace}, but none exists"
        )

    subscription = llmd_runtime.desired_subscription(operator_spec)
    llmd_runtime.oc("apply", "-f", "-", input_text=json.dumps(subscription))

    def _subscription_reconciled() -> dict[str, object] | None:
        payload = llmd_runtime.oc_get_json(
            "subscription.operators.coreos.com",
            name=package,
            namespace=namespace,
        )
        if llmd_runtime.subscription_spec_matches(payload.get("spec", {}), subscription["spec"]):
            return payload
        return None

    llmd_runtime.wait_until(
        f"subscription/{package} reconciliation in {namespace}",
        timeout_seconds=60,
        interval_seconds=5,
        predicate=_subscription_reconciled,
    )
    llmd_runtime.wait_for_operator_csv(
        package,
        namespace,
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )


def prepare_cert_manager(*, platform: dict) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(
        platform, "openshift-cert-manager-operator"
    )
    ensure_operator_subscription(operator_spec)


def prepare_leader_worker_set(*, platform: dict) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(platform, "leader-worker-set")
    ensure_operator_subscription(operator_spec)


def prepare_nfd(*, platform: dict) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(platform, "nfd")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    bootstrap_nfd_instance.run(
        gpu_label_selectors=",".join(platform["cluster"]["nfd_gpu_detection_labels"]),
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )


def prepare_gpu_operator(*, platform: dict) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(platform, "gpu-operator-certified")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    bootstrap_gpu_clusterpolicy.run(
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )


def prepare_rhoai_operator(*, platform: dict) -> None:
    prepare_rhcl_operator(platform=platform)
    deploy_rhoai_custom_catalog(rhoai=platform["rhoai"])
    operator_spec = llmd_runtime.operator_spec_by_package(platform, "rhods-operator")
    operator_spec = rhoai_operator_spec(rhoai=platform["rhoai"], operator_spec=operator_spec)
    ensure_operator_subscription(operator_spec)
    ensure_required_crds(
        crd_names=platform["rhoai"]["required_crds_before_dsc"],
        rhoai=platform["rhoai"],
    )


def ensure_required_crds(*, crd_names: list[str], rhoai: dict) -> None:
    for crd_name in crd_names:
        llmd_runtime.wait_for_crd(
            crd_name,
            timeout_seconds=rhoai["wait_timeout_seconds"],
        )


def apply_datasciencecluster(*, config_dir: str, rhoai: dict) -> None:
    apply_datasciencecluster_command.run(
        config_dir=config_dir,
        rhoai=rhoai,
    )


def wait_for_datasciencecluster_ready(*, rhoai: dict) -> None:
    wait_datasciencecluster_ready_command.run(rhoai=rhoai)


def ensure_gateway(*, config_dir: str, gateway: dict) -> None:
    ensure_gateway_command.run(
        config_dir=config_dir,
        gateway=gateway,
    )


def ensure_test_namespace(*, namespace: str) -> None:
    llmd_runtime.ensure_namespace(
        namespace,
        labels={
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        },
    )


def cleanup_previous_run(
    *,
    namespace: str,
    inference_service_name: str,
    cleanup_timeout_seconds: int,
    benchmark_name: str | None,
) -> None:
    cleanup_toolbox_run(
        namespace=namespace,
        inference_service_name=inference_service_name,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        benchmark_name=benchmark_name,
    )


def prepare_model_cache(
    *,
    namespace: str,
    namespace_is_managed: bool,
    model_key: str,
    model: dict,
    model_cache: dict,
) -> None:
    prepare_model_cache_toolbox_run(
        namespace=namespace,
        namespace_is_managed=namespace_is_managed,
        model_key=model_key,
        model=model,
        model_cache=model_cache,
    )


def verify_gpu_nodes(*, platform: dict) -> None:
    selector = platform["cluster"]["gpu_node_label_selector"]
    data = llmd_runtime.oc_get_json("nodes", selector=selector, ignore_not_found=True)
    items = data.get("items", []) if data else []
    if not items:
        raise RuntimeError(
            f"No GPU nodes found with selector {selector}. The llm_d smoke path requires GPUs."
        )


def capture_prepare_state(*, artifact_dir: Path, namespace: str, platform: dict) -> None:
    artifacts_dir = artifact_dir / "artifacts"
    rhoai = platform["rhoai"]
    gateway = platform["gateway"]

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
    if platform["artifacts"]["capture_namespace_events"]:
        capture_namespace_events(namespace, artifacts_dir / "namespace.events.txt")


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
