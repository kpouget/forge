#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
from pathlib import Path

from projects.cluster.toolbox.cluster_deploy_operator import main as cluster_deploy_operator
from projects.cluster.toolbox.deploy_custom_catalog import main as deploy_custom_catalog
from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime, phase_inputs
from projects.llm_d.toolbox.apply_datasciencecluster import main as apply_datasciencecluster_command
from projects.llm_d.toolbox.bootstrap_gpu_clusterpolicy import main as bootstrap_gpu_clusterpolicy
from projects.llm_d.toolbox.bootstrap_nfd_instance import main as bootstrap_nfd_instance
from projects.llm_d.toolbox.cleanup import main as cleanup_toolbox
from projects.llm_d.toolbox.ensure_gateway import main as ensure_gateway_command
from projects.llm_d.toolbox.prepare_model_cache import main as prepare_model_cache
from projects.llm_d.toolbox.wait_datasciencecluster_ready import (
    main as wait_datasciencecluster_ready_command,
)

LOGGER = logging.getLogger(__name__)


def run(
    *,
    config_dir: str,
    preset_name: str,
    namespace: str,
    namespace_is_managed: bool,
    platform: dict,
    model_key: str,
    model: dict,
    model_cache: dict,
    benchmark: dict | None = None,
) -> int:
    """Prepare a cluster for llm_d downstream smoke and benchmark runs.

    Args:
        config_dir: Configuration directory
        preset_name: Selected preset name
        namespace: Namespace used by llm_d
        namespace_is_managed: Whether namespace lifecycle is managed by llm_d
        platform: Platform configuration
        model_key: Selected model key
        model: Selected model configuration
        model_cache: Model-cache configuration
        benchmark: Optional benchmark configuration
    """

    llmd_runtime.init()
    execute_tasks(locals())
    return 0


def _prepare_inputs(args) -> phase_inputs.PrepareInputs:
    return phase_inputs.build_prepare_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=args.config_dir,
        preset_name=args.preset_name,
        namespace=args.namespace,
        namespace_is_managed=args.namespace_is_managed,
        platform=args.platform,
        model_key=args.model_key,
        model=args.model,
        model_cache=args.model_cache,
        benchmark=args.benchmark,
    )


@task
def load_inputs(args, ctx):
    """Record the prepare inputs"""

    ctx.preset_name = args.preset_name
    ctx.namespace = args.namespace
    return f"Loaded prepare inputs for preset {ctx.preset_name}"


@task
def verify_oc_access_task(args, ctx):
    """Verify OpenShift CLI access"""

    llmd_runtime.oc("whoami", capture_output=True)
    return "OpenShift CLI access verified"


@task
def verify_cluster_version_task(args, ctx):
    """Validate the cluster version against llm_d requirements"""

    version_info = llmd_runtime.oc("version", "-o", "json", capture_output=True)
    payload = json.loads(version_info.stdout)

    openshift_version = (
        payload.get("openshiftVersion")
        or payload.get("serverVersion", {}).get("gitVersion")
        or payload.get("serverVersion", {}).get("platform")
    )
    if not openshift_version:
        raise RuntimeError("Could not determine OpenShift version from `oc version -o json`")

    minimum = args.platform["cluster"]["minimum_openshift_version"]
    if llmd_runtime.version_tuple(openshift_version) < llmd_runtime.version_tuple(minimum):
        raise RuntimeError(
            f"Cluster version {openshift_version} is older than the llm_d minimum {minimum}"
        )

    return f"Cluster version satisfies {minimum}"


@task
def prepare_cert_manager_task(args, ctx):
    """Ensure the cert-manager operator is installed"""

    operator_spec = llmd_runtime.operator_spec_by_package(
        args.platform, "openshift-cert-manager-operator"
    )
    ensure_operator_subscription(operator_spec)
    return "cert-manager operator ready"


@task
def prepare_leader_worker_set_task(args, ctx):
    """Ensure the leader-worker-set operator is installed"""

    operator_spec = llmd_runtime.operator_spec_by_package(args.platform, "leader-worker-set")
    ensure_operator_subscription(operator_spec)
    return "leader-worker-set operator ready"


@task
def prepare_nfd_task(args, ctx):
    """Ensure Node Feature Discovery is installed and reporting GPU labels"""

    config = _prepare_inputs(args)
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "nfd")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    bootstrap_nfd_instance.run(
        gpu_label_selectors=",".join(config.platform["cluster"]["nfd_gpu_detection_labels"]),
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    return "Node Feature Discovery ready"


@task
def prepare_gpu_operator_task(args, ctx):
    """Ensure the GPU operator is installed and ready"""

    config = _prepare_inputs(args)
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "gpu-operator-certified")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    bootstrap_gpu_clusterpolicy.run(
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    return "GPU operator ready"


@task
def prepare_rhoai_operator_task(args, ctx):
    """Ensure the RHOAI operator and its platform dependencies are installed"""

    config = _prepare_inputs(args)
    prepare_rhcl_operator(config)
    deploy_rhoai_custom_catalog(config)
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "rhods-operator")
    operator_spec = rhoai_operator_spec(config, operator_spec)
    ensure_operator_subscription(operator_spec)
    for crd_name in config.platform["rhoai"]["required_crds_before_dsc"]:
        llmd_runtime.wait_for_crd(
            crd_name,
            timeout_seconds=config.platform["rhoai"]["wait_timeout_seconds"],
        )
    return "RHOAI operator ready"


@task
def apply_datasciencecluster_task(args, ctx):
    """Apply the DataScienceCluster manifest"""

    apply_datasciencecluster_command.run(**phase_inputs.prepare_kwargs(_prepare_inputs(args)))
    return "DataScienceCluster applied"


@task
def wait_for_datasciencecluster_ready_task(args, ctx):
    """Wait for the DataScienceCluster to become ready"""

    wait_datasciencecluster_ready_command.run(**phase_inputs.prepare_kwargs(_prepare_inputs(args)))
    return "DataScienceCluster ready"


@task
def ensure_required_crds_task(args, ctx):
    """Wait for the llm_d-required CRDs to exist"""

    for crd_name in args.platform["rhoai"]["required_crds_after_dsc"]:
        llmd_runtime.wait_for_crd(
            crd_name,
            timeout_seconds=args.platform["rhoai"]["wait_timeout_seconds"],
        )
    return "Required CRDs present"


@task
def ensure_gateway_task(args, ctx):
    """Ensure the gateway exists and is programmed"""

    ensure_gateway_command.run(**phase_inputs.prepare_kwargs(_prepare_inputs(args)))
    return "Gateway ready"


@task
def ensure_test_namespace_task(args, ctx):
    """Ensure the llm_d namespace exists"""

    llmd_runtime.ensure_namespace(
        args.namespace,
        labels={
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        },
    )
    return f"Namespace {args.namespace} ready"


@task
def cleanup_previous_run_task(args, ctx):
    """Delete leftover llm_d resources from the namespace"""

    config = _prepare_inputs(args)
    cleanup_toolbox.run(**phase_inputs.cleanup_kwargs(config))
    return f"Previous llm_d leftovers deleted from {config.namespace}"


@task
def prepare_model_cache_task(args, ctx):
    """Prepare the shared model cache if enabled"""

    config = _prepare_inputs(args)
    prepare_model_cache.run(**phase_inputs.prepare_model_cache_kwargs(config))
    return "Model cache prepared"


@task
def verify_gpu_nodes_task(args, ctx):
    """Verify that GPU nodes are available on the cluster"""

    selector = args.platform["cluster"]["gpu_node_label_selector"]
    data = llmd_runtime.oc_get_json("nodes", selector=selector, ignore_not_found=True)
    items = data.get("items", []) if data else []
    if not items:
        raise RuntimeError(
            f"No GPU nodes found with selector {selector}. The llm_d smoke path requires GPUs."
        )
    return "GPU nodes detected"


@task
def capture_prepare_state_task(args, ctx):
    """Capture cluster state after the prepare phase"""

    config = _prepare_inputs(args)
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
    return "Prepare-state artifacts captured"


def verify_oc_access() -> None:
    llmd_runtime.oc("whoami", capture_output=True)


def verify_cluster_version(config: phase_inputs.PrepareInputs) -> None:
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
    return cluster_deploy_operator.run(
        package_name=operator_spec["package"],
        target_namespace=operator_spec["namespace"],
        source_name=operator_spec["source"],
        channel=operator_spec["channel"],
        source_namespace=operator_spec.get("source_namespace", "openshift-marketplace"),
        wait_timeout_seconds=operator_spec["wait_timeout_seconds"],
        display_name=operator_spec.get("display_name", operator_spec["package"]),
    )


def deploy_rhoai_custom_catalog(config: phase_inputs.PrepareInputs) -> int:
    custom_catalog = config.platform["rhoai"]["custom_catalog"]
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
    config: phase_inputs.PrepareInputs,
    operator_spec: dict[str, str],
) -> dict[str, str]:
    custom_catalog = config.platform["rhoai"]["custom_catalog"]
    if not custom_catalog["enabled"]:
        return operator_spec

    updated_spec = dict(operator_spec)
    updated_spec["source"] = custom_catalog["name"]
    updated_spec["source_namespace"] = custom_catalog["namespace"]
    return updated_spec


def prepare_rhcl_operator(config: phase_inputs.PrepareInputs) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "rhcl-operator")
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


def prepare_cert_manager(config: phase_inputs.PrepareInputs) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(
        config.platform, "openshift-cert-manager-operator"
    )
    ensure_operator_subscription(operator_spec)


def prepare_leader_worker_set(config: phase_inputs.PrepareInputs) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "leader-worker-set")
    ensure_operator_subscription(operator_spec)


def prepare_nfd(config: phase_inputs.PrepareInputs) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "nfd")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    bootstrap_nfd_instance.run(
        gpu_label_selectors=",".join(config.platform["cluster"]["nfd_gpu_detection_labels"]),
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )


def prepare_gpu_operator(config: phase_inputs.PrepareInputs) -> None:
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "gpu-operator-certified")
    ensure_operator_subscription(operator_spec)
    llmd_runtime.wait_for_crd(
        operator_spec["bootstrap_crd"],
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )
    bootstrap_gpu_clusterpolicy.run(
        timeout_seconds=operator_spec["wait_timeout_seconds"],
    )


def prepare_rhoai_operator(config: phase_inputs.PrepareInputs) -> None:
    prepare_rhcl_operator(config)
    deploy_rhoai_custom_catalog(config)
    operator_spec = llmd_runtime.operator_spec_by_package(config.platform, "rhods-operator")
    operator_spec = rhoai_operator_spec(config, operator_spec)
    ensure_operator_subscription(operator_spec)
    ensure_required_crds(config.platform["rhoai"]["required_crds_before_dsc"], config)


def ensure_required_crds(crd_names: list[str], config: phase_inputs.PrepareInputs) -> None:
    for crd_name in crd_names:
        llmd_runtime.wait_for_crd(
            crd_name,
            timeout_seconds=config.platform["rhoai"]["wait_timeout_seconds"],
        )


def apply_datasciencecluster(config: phase_inputs.PrepareInputs) -> None:
    apply_datasciencecluster_command.run(**phase_inputs.prepare_kwargs(config))


def wait_for_datasciencecluster_ready(config: phase_inputs.PrepareInputs) -> None:
    wait_datasciencecluster_ready_command.run(**phase_inputs.prepare_kwargs(config))


def ensure_gateway(config: phase_inputs.PrepareInputs) -> None:
    ensure_gateway_command.run(**phase_inputs.prepare_kwargs(config))


def ensure_test_namespace(config: phase_inputs.PrepareInputs) -> None:
    llmd_runtime.ensure_namespace(
        config.namespace,
        labels={
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llm_d",
        },
    )


def verify_gpu_nodes(config: phase_inputs.PrepareInputs) -> None:
    selector = config.platform["cluster"]["gpu_node_label_selector"]
    data = llmd_runtime.oc_get_json("nodes", selector=selector, ignore_not_found=True)
    items = data.get("items", []) if data else []
    if not items:
        raise RuntimeError(
            f"No GPU nodes found with selector {selector}. The llm_d smoke path requires GPUs."
        )


def capture_prepare_state(config: phase_inputs.PrepareInputs) -> None:
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
