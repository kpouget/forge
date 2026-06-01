#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import always, execute_tasks, shell, task, template, toolbox
from projects.core.dsl.utils.k8s import (
    oc_get_json,
    resource_exists,
    wait_until,
)
from projects.llm_d.runtime.runtime_config import init as runtime_init

NFD_NAME = "nfd-instance"
NFD_NAMESPACE = "openshift-nfd"


def run(*, gpu_label_selectors: str, timeout_seconds: int = 900) -> int:
    """
    Bootstrap the NodeFeatureDiscovery instance used by llm_d and wait for GPU labels.

    Args:
        gpu_label_selectors: Comma-separated list of node label selectors to probe
        timeout_seconds: Maximum time to wait for the NFD resource and GPU labels
    """

    runtime_init()
    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    ctx.selectors = [item.strip() for item in args.gpu_label_selectors.split(",") if item.strip()]
    if not ctx.selectors:
        raise ValueError("gpu_label_selectors must contain at least one selector")
    return f"Prepared NFD bootstrap command with {len(ctx.selectors)} GPU selectors"


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


@task
def wait_for_nfd_resource(args, ctx):
    """Wait for the NodeFeatureDiscovery resource to exist"""

    wait_until(
        f"NodeFeatureDiscovery/{NFD_NAME} in {NFD_NAMESPACE}",
        timeout_seconds=args.timeout_seconds,
        interval_seconds=10,
        predicate=lambda: resource_exists(
            "nodefeaturediscovery",
            NFD_NAME,
            namespace=NFD_NAMESPACE,
        ),
    )
    return f"NodeFeatureDiscovery/{NFD_NAME} exists"


@task
def wait_for_gpu_labels(args, ctx):
    """Wait for any configured GPU label selector to match cluster nodes"""

    def _labels_present() -> bool:
        for selector in ctx.selectors:
            data = oc_get_json("nodes", selector=selector, ignore_not_found=True)
            if data and data.get("items"):
                return True
        return False

    wait_until(
        "NFD GPU discovery labels on cluster nodes",
        timeout_seconds=args.timeout_seconds,
        interval_seconds=15,
        predicate=_labels_present,
    )
    return "GPU discovery labels detected"


@always
@task
def capture_nfd_state(args, ctx):
    """Capture the NodeFeatureDiscovery resource and matching nodes"""

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


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
