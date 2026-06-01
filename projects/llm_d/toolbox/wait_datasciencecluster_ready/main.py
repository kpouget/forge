#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import execute_tasks, task, toolbox
from projects.core.dsl.utils.k8s import (
    oc_get_json,
    wait_until,
)
from projects.llm_d.runtime.runtime_config import init as runtime_init


def run(
    *,
    rhoai: dict,
) -> int:
    """
    Wait for the llm_d DataScienceCluster to become ready.

    Args:
        rhoai: RHOAI configuration block
    """

    runtime_init()
    execute_tasks(locals())
    return 0


@task
def wait_for_datasciencecluster_ready(args, ctx):
    """Wait for the DataScienceCluster phase to become Ready"""

    rhoai = args.rhoai

    def _dsc_ready() -> bool:
        payload = oc_get_json(
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

    wait_until(
        f"datasciencecluster/{rhoai['datasciencecluster_name']} ready",
        timeout_seconds=rhoai["wait_timeout_seconds"],
        interval_seconds=10,
        predicate=_dsc_ready,
    )
    return "DataScienceCluster ready"


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
