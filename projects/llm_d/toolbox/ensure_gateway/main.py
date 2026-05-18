#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime, phase_inputs


def run(
    *,
    config_dir: str,
    gateway: dict,
) -> int:
    """
    Ensure the llm_d gateway exists and is programmed.

    Args:
        config_dir: Configuration directory
        gateway: Gateway configuration block
    """

    llmd_runtime.init()
    execute_tasks(locals())
    return 0


@task
def ensure_gateway(args, ctx):
    """Ensure the gateway exists and reaches Programmed=True"""

    config = phase_inputs.build_prepare_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=args.config_dir,
        preset_name="ensure-gateway",
        namespace="unused",
        namespace_is_managed=False,
        platform={"gateway": args.gateway},
        model_key="unused",
        model={},
        model_cache={},
        benchmark=None,
    )
    gateway = config.platform["gateway"]
    if not llmd_runtime.resource_exists("gateway", gateway["name"], namespace=gateway["namespace"]):
        if not gateway["create_if_missing"]:
            raise RuntimeError(
                f"Required gateway {gateway['name']} does not exist in {gateway['namespace']}"
            )
        manifest = llmd_runtime.render_gateway(config)
        llmd_runtime.apply_manifest(config.artifact_dir / "src" / "gateway.yaml", manifest)

    def _gateway_programmed() -> bool:
        resource = llmd_runtime.oc_get_json(
            "gateway",
            name=gateway["name"],
            namespace=gateway["namespace"],
        )
        return llmd_runtime.condition_status(resource, "Programmed") == "True"

    llmd_runtime.wait_until(
        f"gateway/{gateway['name']} programmed",
        timeout_seconds=gateway["wait_timeout_seconds"],
        interval_seconds=10,
        predicate=_gateway_programmed,
    )
    return "Gateway ready"


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
