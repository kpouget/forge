#!/usr/bin/env python3

from __future__ import annotations

import json
import logging

from projects.core.dsl import always, execute_tasks, retry, shell, task, template, toolbox

logger = logging.getLogger("DSL")


def run(
    package_name: str,
    target_namespace: str,
    source_name: str,
    channel: str,
    *,
    source_namespace: str = "openshift-marketplace",
    wait_timeout_seconds: int = 900,
    installplan_approval: str = "Automatic",
    display_name: str = "",
) -> int:
    """
    Deploy an OLM operator and wait for its CSV to succeed.

    Args:
        package_name: Operator package/subscription name
        target_namespace: Namespace where the operator will be installed
        source_name: CatalogSource name providing the operator
        channel: Subscription channel to use
        source_namespace: CatalogSource namespace
        wait_timeout_seconds: Maximum time to wait for readiness
        installplan_approval: InstallPlan approval mode
        display_name: Optional human-friendly name used in logs
    """

    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    ctx.display_name = args.display_name or args.package_name
    return f"Prepared directories for {ctx.display_name}"


@task
def validate_parameters(args, ctx):
    """Validate command parameters"""

    if not args.package_name:
        raise ValueError("package_name is required")
    if not args.target_namespace:
        raise ValueError("target_namespace is required")
    if not args.source_name:
        raise ValueError("source_name is required")
    if not args.channel:
        raise ValueError("channel is required")
    if args.installplan_approval not in {"Automatic", "Manual"}:
        raise ValueError("installplan_approval must be either 'Automatic' or 'Manual'")

    return (
        f"Validated deployment parameters for {ctx.display_name} "
        f"from {args.source_name}/{args.source_namespace}"
    )


@retry(attempts=60, delay=15)
@task
def wait_for_catalog_source_ready(args, ctx):
    """Wait for the CatalogSource to report READY"""

    result = shell.run(
        "oc get catalogsource "
        f"{args.source_name} -n {args.source_namespace} "
        "-o jsonpath='{.status.connectionState.lastObservedState}'",
        check=False,
        log_stdout=False,
    )
    if not result.success:
        return False

    state = result.stdout.strip()
    if state == "READY":
        return f"CatalogSource {args.source_name} is READY"
    return False


@retry(attempts=60, delay=15)
@task
def wait_for_package_manifest(args, ctx):
    """Wait for the operator PackageManifest to become available"""

    result = shell.run(
        f"oc get packagemanifest {args.package_name} -n {args.source_namespace} -o json",
        check=False,
        log_stdout=False,
    )
    if not result.success:
        return False

    ctx.package_manifest = json.loads(result.stdout)
    return f"PackageManifest {args.package_name} is available"


@task
def render_namespace_manifest(args, ctx):
    """Render the target namespace manifest"""

    ctx.namespace_manifest_file = (
        args.artifact_dir / "src" / f"{args.target_namespace}-namespace.yaml"
    )
    template.render_template_to_file("namespace.yaml.j2", ctx.namespace_manifest_file)
    return f"Rendered namespace manifest for {args.target_namespace}"


@task
def apply_namespace(args, ctx):
    """Apply the target namespace manifest"""

    shell.run(f"oc apply -f {ctx.namespace_manifest_file}")
    return f"Ensured namespace {args.target_namespace}"


@task
def render_operator_group_manifest(args, ctx):
    """Render the OperatorGroup manifest, unless a suitable one already exists"""

    result = shell.run(
        f"oc get operatorgroup -n {args.target_namespace} -o json",
        check=False,
        log_stdout=False,
    )
    if result.success:
        operator_groups = json.loads(result.stdout).get("items", [])
        for operator_group in operator_groups:
            spec = operator_group.get("spec", {})
            target_namespaces = spec.get("targetNamespaces", [])
            if not target_namespaces or args.target_namespace in target_namespaces:
                ctx.operator_group_manifest_file = None
                ctx.operator_group_name = operator_group["metadata"]["name"]
                return (
                    f"Reusing existing OperatorGroup {ctx.operator_group_name} "
                    f"in {args.target_namespace}"
                )

        if operator_groups:
            names = ", ".join(
                operator_group["metadata"]["name"] for operator_group in operator_groups
            )
            raise RuntimeError(
                f"Namespace {args.target_namespace} already has OperatorGroups "
                f"that do not target it: {names}"
            )

    ctx.operator_group_manifest_file = (
        args.artifact_dir / "src" / f"{args.package_name}-operatorgroup.yaml"
    )
    template.render_template_to_file("operator_group.yaml.j2", ctx.operator_group_manifest_file)
    return f"Rendered OperatorGroup manifest for {args.package_name}"


@task
def apply_operator_group(args, ctx):
    """Apply the OperatorGroup manifest"""

    if ctx.operator_group_manifest_file is None:
        return f"Using existing OperatorGroup {ctx.operator_group_name}"

    shell.run(f"oc apply -f {ctx.operator_group_manifest_file}")
    return f"Ensured OperatorGroup in {args.target_namespace}"


@task
def render_subscription_manifest(args, ctx):
    """Render the Subscription manifest"""

    ctx.subscription_manifest_file = (
        args.artifact_dir / "src" / f"{args.package_name}-subscription.yaml"
    )
    template.render_template_to_file("subscription.yaml.j2", ctx.subscription_manifest_file)
    return f"Rendered Subscription manifest for {args.package_name}"


@task
def apply_subscription(args, ctx):
    """Apply the Subscription manifest"""

    shell.run(f"oc apply -f {ctx.subscription_manifest_file}")
    return f"Applied Subscription for {ctx.display_name}"


@retry(attempts=60, delay=15)
@task
def wait_for_csv_ready(args, ctx):
    """Wait for the installed CSV to reach Succeeded phase"""

    result = shell.run(
        "oc get csv "
        f"-n {args.target_namespace} "
        f"-l operators.coreos.com/{args.package_name}.{args.target_namespace} "
        "-o json",
        check=False,
        log_stdout=False,
    )
    if not result.success:
        return False

    payload = json.loads(result.stdout)
    items = payload.get("items", [])
    if not items:
        return False

    csv = items[0]
    ctx.csv_name = csv["metadata"]["name"]
    phase = csv.get("status", {}).get("phase")
    if phase == "Succeeded":
        return f"CSV {ctx.csv_name} succeeded"
    if phase in {"Failed", "Replacing"}:
        logger.info("CSV %s currently in phase %s", ctx.csv_name, phase)
    return False


@always
@task
def capture_catalog_source(args, ctx):
    """Capture CatalogSource YAML for diagnostics"""

    shell.run(
        f"oc get catalogsource {args.source_name} -n {args.source_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "catalogsource.yaml",
        check=False,
    )
    return "Captured CatalogSource state"


@always
@task
def capture_package_manifest(args, ctx):
    """Capture PackageManifest YAML and JSON"""

    shell.run(
        f"oc get packagemanifest {args.package_name} -n {args.source_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "packagemanifest.yaml",
        check=False,
    )
    shell.run(
        f"oc get packagemanifest {args.package_name} -n {args.source_namespace} -o json",
        stdout_dest=args.artifact_dir / "artifacts" / "packagemanifest.json",
        check=False,
    )
    return "Captured PackageManifest state"


@always
@task
def capture_operator_group(args, ctx):
    """Capture OperatorGroup YAML"""

    shell.run(
        f"oc get operatorgroup -n {args.target_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "operatorgroup.yaml",
        check=False,
    )
    return "Captured OperatorGroup state"


@always
@task
def capture_subscription(args, ctx):
    """Capture Subscription YAML"""

    shell.run(
        f"oc get subscription {args.package_name} -n {args.target_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "subscription.yaml",
        check=False,
    )
    return "Captured Subscription state"


@always
@task
def capture_csv(args, ctx):
    """Capture installed CSV YAML when known"""

    csv_name = getattr(ctx, "csv_name", "")
    if not csv_name:
        result = shell.run(
            "oc get csv "
            f"-n {args.target_namespace} "
            f"-l operators.coreos.com/{args.package_name}.{args.target_namespace} "
            "-o jsonpath='{.items[0].metadata.name}'",
            check=False,
            log_stdout=False,
        )
        csv_name = result.stdout.strip()
        if not csv_name:
            return "No CSV found to capture"

    shell.run(
        f"oc get csv {csv_name} -n {args.target_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "csv.yaml",
        check=False,
    )
    return f"Captured CSV {csv_name}"


@always
@task
def capture_target_namespace_pods(args, ctx):
    """Capture pods from the operator target namespace"""

    shell.run(
        f"oc get pods -n {args.target_namespace} -o wide",
        stdout_dest=args.artifact_dir / "artifacts" / "pods.status",
        check=False,
    )
    shell.run(
        f"oc get pods -n {args.target_namespace} -o yaml",
        stdout_dest=args.artifact_dir / "artifacts" / "pods.yaml",
        check=False,
    )
    return "Captured target namespace pods"


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
