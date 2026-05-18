#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime, phase_inputs


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
    """
    Apply the llm_d DataScienceCluster manifest.

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


@task
def apply_datasciencecluster(args, ctx):
    """Render and apply the DataScienceCluster manifest"""

    config = phase_inputs.build_prepare_inputs(
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
    return "DataScienceCluster applied"


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
