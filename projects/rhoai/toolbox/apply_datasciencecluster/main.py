#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import (
    apply_manifest,
    oc,
)
from projects.rhoai.toolbox.apply_datasciencecluster.utils import render_datasciencecluster


@entrypoint
def run(
    *,
    datasciencecluster_name: str,
    namespace: str,
    components: list[str],
) -> int:
    """
    Apply the llm_d DataScienceCluster manifest.

    Args:
        datasciencecluster_name: Name of the DataScienceCluster
        namespace: Namespace for the DataScienceCluster
        components: List of components to enable (e.g., ["kserve", "codeflare"])
    """

    execute_tasks(locals())
    return 0


@task
def apply_datasciencecluster(args, ctx):
    """Render and apply the DataScienceCluster manifest"""

    manifest = render_datasciencecluster(
        datasciencecluster_name=args.datasciencecluster_name,
        namespace=args.namespace,
        components=args.components,
    )

    # Ensure the src directory exists
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    apply_manifest(src_dir / "datasciencecluster.yaml", manifest)
    oc(
        "get",
        "datasciencecluster",
        args.datasciencecluster_name,
        "-n",
        args.namespace,
        "-o",
        "yaml",
        capture_output=True,
        log_stdout=False,
    )
    return "DataScienceCluster applied"


if __name__ == "__main__":
    run.main()
