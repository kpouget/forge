"""
Deploy MCP-compatible mock servers on Kubernetes.

Supports both single-server (count=1) and scale-out (count>1) scenarios
using programmatic manifest generation. Each server gets a unique name
(e.g. mock-server-1 .. mock-server-N), its own Service, and configurable
tool count.

All resources are labeled for easy bulk cleanup between test levels.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from projects.core.dsl.utils.k8s import oc

logger = logging.getLogger(__name__)

SCALE_OUT_LABEL = "experiment=scale-out"


def deploy_servers(
    *,
    namespace: str,
    count: int,
    image: str,
    name_prefix: str = "mock-server",
    tools_per_server: int = 10,
    labels: dict[str, str] | None = None,
    node_selector: dict[str, str] | None = None,
    tolerations: list[dict[str, str]] | None = None,
    rollout_timeout: str = "120s",
) -> list[str]:
    """
    Deploy `count` mock servers ({name_prefix}-1 .. {name_prefix}-N).

    Returns the list of deployed server names.
    All manifests are applied in a single batch, then readiness is
    checked for maximum throughput.
    """
    merged_labels = {"experiment": "scale-out"}
    if labels:
        merged_labels.update(labels)

    logger.info("Deploying %d mock server(s) with %d tools each...", count, tools_per_server)

    names = [f"{name_prefix}-{i}" for i in range(1, count + 1)]
    all_manifests = []
    for name in names:
        manifest = _generate_server_manifest(
            name=name,
            namespace=namespace,
            image=image,
            tools_per_server=tools_per_server,
            labels=merged_labels,
            node_selector=node_selector,
            tolerations=tolerations,
        )
        all_manifests.append(manifest)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write("---\n".join(all_manifests))
        tmp_path = tmp.name
    oc("apply", "-f", tmp_path)
    Path(tmp_path).unlink(missing_ok=True)
    logger.info("All %d server manifests applied", count)

    logger.info("Waiting for %d server(s) to become ready...", count)
    oc(
        "rollout",
        "status",
        *[f"deployment/{n}" for n in names],
        "-n",
        namespace,
        f"--timeout={rollout_timeout}",
        check=False,
    )

    result = oc(
        "get",
        "deployment",
        "-n",
        namespace,
        "-l",
        SCALE_OUT_LABEL,
        "-o",
        "jsonpath={.items[?(@.status.readyReplicas==1)].metadata.name}",
        check=False,
    )
    ready_count = len(result.stdout.split()) if result.stdout.strip() else 0
    logger.info("Servers ready: %d/%d", ready_count, count)
    if ready_count < count:
        raise RuntimeError(f"Only {ready_count}/{count} servers became ready")
    return names


def restart_servers(
    *,
    namespace: str,
    count: int = 1,
    name_prefix: str = "mock-server",
    rollout_timeout: str = "120s",
) -> None:
    """Restart mock server deployment(s) for clean state between tests."""
    names = [f"{name_prefix}-{i}" for i in range(1, count + 1)]
    for name in names:
        logger.info("Restarting deployment/%s in %s", name, namespace)
        oc("rollout", "restart", f"deployment/{name}", "-n", namespace, check=False)

    for name in names:
        oc(
            "rollout",
            "status",
            f"deployment/{name}",
            "-n",
            namespace,
            f"--timeout={rollout_timeout}",
        )
    logger.info("All %d server(s) restarted and ready", len(names))


def cleanup_servers(*, namespace: str) -> None:
    """Delete all scale-out mock server deployments and services by label."""
    logger.info("Cleaning up scale-out servers (label=%s)...", SCALE_OUT_LABEL)
    oc(
        "delete",
        "deployment,service",
        "-n",
        namespace,
        "-l",
        SCALE_OUT_LABEL,
        "--wait=false",
        "--ignore-not-found=true",
        check=False,
    )
    _wait_for_deletion(namespace=namespace, resource="deployment", timeout_seconds=120)
    logger.info("Scale-out server cleanup complete")


def _wait_for_deletion(*, namespace: str, resource: str, timeout_seconds: int = 120) -> None:
    """Wait until no resources with the scale-out label remain."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = oc(
            "get",
            resource,
            "-n",
            namespace,
            "-l",
            SCALE_OUT_LABEL,
            "--no-headers",
            check=False,
        )
        remaining = (
            len([line for line in result.stdout.strip().split("\n") if line.strip()])
            if result.stdout.strip()
            else 0
        )
        if remaining == 0:
            return
        time.sleep(3)
    logger.warning("Timed out waiting for %s deletion", resource)


def _generate_server_manifest(
    *,
    name: str,
    namespace: str,
    image: str,
    tools_per_server: int,
    labels: dict[str, str],
    node_selector: dict[str, str] | None = None,
    tolerations: list[dict[str, str]] | None = None,
) -> str:
    """Generate a YAML manifest for a single mock server Deployment + Service."""
    label_set = {"app": name}
    label_set.update(labels)

    pod_spec: dict[str, Any] = {
        "containers": [
            {
                "name": "server",
                "image": image,
                "imagePullPolicy": "Always",
                "args": ["--addr", ":8080"],
                "env": [
                    {"name": "GOGC", "value": "off"},
                    {"name": "NUM_TOOLS", "value": str(tools_per_server)},
                ],
                "ports": [{"containerPort": 8080, "name": "http"}],
                "readinessProbe": {
                    "tcpSocket": {"port": 8080},
                    "initialDelaySeconds": 2,
                    "periodSeconds": 5,
                    "timeoutSeconds": 2,
                },
            }
        ],
    }
    if node_selector:
        pod_spec["nodeSelector"] = node_selector
    if tolerations:
        pod_spec["tolerations"] = tolerations

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": label_set,
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": label_set},
                "spec": pod_spec,
            },
        },
    }

    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": label_set,
        },
        "spec": {
            "ports": [{"name": "http", "port": 8080, "targetPort": 8080}],
            "selector": {"app": name},
            "type": "ClusterIP",
        },
    }

    return yaml.safe_dump_all([deployment, service], sort_keys=False)
