#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import (
    condition_status,
    oc_apply,
    oc_get_json,
    oc_resource_exists,
)
from projects.llm_d.toolbox.ensure_gateway.utils import render_gateway


@entrypoint
def run(
    *,
    config_dir: str,
    namespace: str,
    name: str,
    gateway_class_name: str,
    status_address_name: str,
    create_if_missing: bool,
) -> int:
    """
    Ensure the llm_d gateway exists and is programmed.

    Args:
        config_dir: Configuration directory
        namespace: Gateway namespace
        name: Gateway name
        gateway_class_name: Gateway class name
        status_address_name: Status address name
        create_if_missing: Whether to create gateway if missing
    """

    execute_tasks(locals())
    return 0


@task
def create_gateway_if_needed(args, ctx):
    """Create gateway if it doesn't exist"""

    ctx.gateway = {
        "namespace": args.namespace,
        "name": args.name,
        "gateway_class_name": args.gateway_class_name,
        "status_address_name": args.status_address_name,
        "create_if_missing": args.create_if_missing,
    }

    if not oc_resource_exists("gateway", args.name, namespace=args.namespace):
        if not args.create_if_missing:
            raise RuntimeError(f"Required gateway {args.name} does not exist in {args.namespace}")

        # Create src directory before writing manifest
        src_dir = args.artifact_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        manifest = render_gateway(
            name=args.name,
            namespace=args.namespace,
            gateway_class_name=args.gateway_class_name,
        )
        oc_apply(src_dir / "gateway.yaml", manifest)
        return f"Created gateway {args.name}"

    return f"Gateway {args.name} already exists"


@retry(attempts=90, delay=10, backoff=1.0)
@task
def wait_gateway_programmed(args, ctx):
    """Wait for gateway to be programmed"""

    resource = oc_get_json(
        "gateway",
        name=ctx.gateway["name"],
        namespace=ctx.gateway["namespace"],
    )
    if condition_status(resource, "Programmed") == "True":
        return f"Gateway {ctx.gateway['name']} programmed"
    return False  # Retry


if __name__ == "__main__":
    run.main()
