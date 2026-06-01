#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import execute_tasks, task, toolbox
from projects.core.dsl.utils.k8s import (
    apply_manifest,
    oc,
)
from projects.llm_d.runtime import llmd_runtime, phase_inputs
from projects.llm_d.runtime.runtime_config import init as runtime_init


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
    manifest = llmd_runtime.render_datasciencecluster(config)
    apply_manifest(config.artifact_dir / "src" / "datasciencecluster.yaml", manifest)
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


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
