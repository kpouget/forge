#!/usr/bin/env python3

from __future__ import annotations

import logging

from projects.core.dsl import always, entrypoint, execute_tasks, retry, shell, task, template
from projects.core.dsl.utils.k8s import (
    resource_exists,
)
from projects.llm_d.runtime.runtime_config import init as runtime_init

NFD_NAME = "nfd-instance"
NFD_NAMESPACE = "openshift-nfd"

logger = logging.getLogger("TOOLBOX")


@entrypoint
def run(*, timeout_seconds: int = 900) -> int:
    """
    Bootstrap the NodeFeatureDiscovery instance and wait for it to be ready.

    Args:
        timeout_seconds: Maximum time to wait for the NFD resource and node labeling
    """

    runtime_init()
    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    return "Prepared NFD bootstrap command"


@task
def render_manifest(args, ctx):
    """Render the NodeFeatureDiscovery manifest"""

    ctx.manifest_file = args.artifact_dir / "src" / "nfd-nodefeaturediscovery.yaml"
    template.render_template_to_file("nodefeaturediscovery.yaml.j2", ctx.manifest_file)
    return "Rendered NodeFeatureDiscovery manifest"


@task
def apply_manifest_if_missing(args, ctx):
    """Apply the NodeFeatureDiscovery manifest when missing"""

    if resource_exists("nodefeaturediscovery", NFD_NAME, namespace=NFD_NAMESPACE):
        return f"NodeFeatureDiscovery/{NFD_NAME} already exists"

    shell.run(f"oc apply -f {ctx.manifest_file}")
    return f"Applied NodeFeatureDiscovery/{NFD_NAME}"


@retry(attempts=90, delay=10)
@task
def wait_for_nfd_resource(args, ctx):
    """Wait for the NodeFeatureDiscovery resource to exist"""

    if resource_exists("nodefeaturediscovery", NFD_NAME, namespace=NFD_NAMESPACE):
        return f"NodeFeatureDiscovery/{NFD_NAME} exists"
    return False


@retry(attempts=60, delay=15)
@task
def wait_for_nfd_ready(args, ctx):
    """Wait for NFD to label all nodes with kernel version"""

    # Get all nodes count
    total_result = shell.run(
        "oc get nodes --no-headers -oname -lnode-role.kubernetes.io/worker",
        check=False,
        log_stdout=False,
    )
    if not total_result.success:
        return False

    total_nodes = len([line for line in total_result.stdout.strip().split("\n") if line.strip()])
    if total_nodes == 0:
        return False

    # Get nodes with NFD kernel version label
    labeled_result = shell.run(
        "oc get nodes -l feature.node.kubernetes.io/kernel-version.major --no-headers -oname",
        check=False,
    )

    if not labeled_result.success:
        labeled_count = 0
    else:
        labeled_count = len(
            [line for line in labeled_result.stdout.strip().split("\n") if line.strip()]
        )

    if labeled_count == total_nodes:
        return f"NFD ready: {total_nodes}/{total_nodes} nodes labeled with kernel version"

    logger.info(f"NFD not ready: {total_nodes}/{total_nodes} nodes labeled with kernel version")

    return False


@always
@task
def capture_nfd_state(args, ctx):
    """Capture the NodeFeatureDiscovery resource and all node labels"""

    shell.run(
        f"oc get nodefeaturediscovery {NFD_NAME} -n {NFD_NAMESPACE} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "nodefeaturediscovery.yaml",
        check=False,
    )
    shell.run(
        "oc get nodes -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "nodes.yaml",
        check=False,
    )
    shell.run(
        "oc get nodes -o wide",
        stdout_dest=args.artifact_dir / "artifacts" / "nodes.status",
        check=False,
    )
    return "Captured NFD bootstrap state"


if __name__ == "__main__":
    run.main()
