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
    Ensure the llm_d gateway exists and is programmed.

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
def ensure_gateway(args, ctx):
    """Ensure the gateway exists and reaches Programmed=True"""

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
