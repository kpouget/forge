#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import always, execute_tasks, shell, task, template, toolbox
from projects.core.dsl.utils.k8s import oc_get_json, resource_exists, wait_until
from projects.llm_d.runtime.runtime_config import init as runtime_init


def run(*, clusterpolicy_name: str = "gpu-cluster-policy", timeout_seconds: int = 1800) -> int:
    """
    Bootstrap the NVIDIA ClusterPolicy used by llm_d and wait for readiness.

    Args:
        clusterpolicy_name: Name of the ClusterPolicy resource
        timeout_seconds: Maximum time to wait for the ClusterPolicy to become ready
    """

    runtime_init()
    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    return f"Prepared GPU ClusterPolicy bootstrap for {args.clusterpolicy_name}"


@task
def render_manifest(args, ctx):
    """Render the ClusterPolicy manifest"""

    ctx.manifest_file = args.artifact_dir / "src" / "gpu-clusterpolicy.yaml"
    template.render_template_to_file("gpu-clusterpolicy.yaml.j2", ctx.manifest_file)
    return f"Rendered ClusterPolicy manifest for {args.clusterpolicy_name}"


@task
def apply_manifest_if_missing(args, ctx):
    """Apply the ClusterPolicy manifest when missing"""

    if resource_exists("clusterpolicy", args.clusterpolicy_name):
        return f"ClusterPolicy/{args.clusterpolicy_name} already exists"

    shell.run(f"oc apply -f {ctx.manifest_file}")
    return f"Applied ClusterPolicy/{args.clusterpolicy_name}"


@task
def wait_for_clusterpolicy_ready(args, ctx):
    """Wait for the ClusterPolicy to report ready"""

    def _clusterpolicy_ready() -> bool:
        payload = oc_get_json("clusterpolicy", name=args.clusterpolicy_name)
        state = payload.get("status", {}).get("state", "")
        return state.lower() == "ready"

    wait_until(
        f"clusterpolicy/{args.clusterpolicy_name} ready",
        timeout_seconds=args.timeout_seconds,
        interval_seconds=15,
        predicate=_clusterpolicy_ready,
    )
    return f"ClusterPolicy/{args.clusterpolicy_name} ready"


@always
@task
def capture_clusterpolicy_state(args, ctx):
    """Capture ClusterPolicy YAML for diagnostics"""

    shell.run(
        f"oc get clusterpolicy {args.clusterpolicy_name} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "clusterpolicy.yaml",
        check=False,
    )
    return f"Captured ClusterPolicy/{args.clusterpolicy_name} state"


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
