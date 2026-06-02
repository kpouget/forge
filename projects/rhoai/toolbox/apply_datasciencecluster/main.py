#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import (
    apply_manifest,
    oc,
)
from projects.llm_d.runtime import phase_inputs
from projects.llm_d.runtime.runtime_config import init as runtime_init
from projects.rhoai.toolbox.apply_datasciencecluster.utils import render_datasciencecluster


@entrypoint
def run(
    *,
    config_dir: str,
    rhoai: dict,
) -> int:
    """
    Apply the llm_d DataScienceCluster manifest.

    Args:
        config_dir: Configuration directory
        rhoai: RHOAI configuration block
    """

    runtime_init()
    execute_tasks(locals())
    return 0


@task
def apply_datasciencecluster(args, ctx):
    """Render and apply the DataScienceCluster manifest"""

    config = phase_inputs.build_prepare_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=args.config_dir,
        preset_name="apply-datasciencecluster",
        namespace="unused",
        namespace_is_managed=False,
        platform={"rhoai": args.rhoai},
        model_key="unused",
        model={},
        model_cache={},
        benchmark=None,
    )
    manifest = render_datasciencecluster(config)

    # Ensure the src directory exists
    src_dir = config.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    apply_manifest(src_dir / "datasciencecluster.yaml", manifest)
    oc(
        "get",
        "datasciencecluster",
        config.platform["rhoai"]["datasciencecluster_name"],
        "-n",
        config.platform["rhoai"]["namespace"],
        "-o",
        "yaml",
        capture_output=True,
    )
    return "DataScienceCluster applied"


if __name__ == "__main__":
    run.main()
