#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime


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
    Wait for the llm_d DataScienceCluster to become ready.

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
def wait_for_datasciencecluster_ready(args, ctx):
    """Wait for the DataScienceCluster phase to become Ready"""

    rhoai = args.platform["rhoai"]

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
    return "DataScienceCluster ready"


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
