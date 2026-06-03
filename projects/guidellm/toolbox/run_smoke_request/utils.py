"""
Utilities for the smoke request toolbox module.
"""

from __future__ import annotations

from typing import Any


def render_smoke_request_pod_from_parts(
    *,
    namespace: str,
    pod_name: str,
    client_image: str,
) -> dict[str, Any]:
    """Render a smoke request pod manifest that sleeps forever.

    Args:
        namespace: Target namespace
        pod_name: Name for the smoke test pod
        client_image: Container image for making HTTP requests

    Returns:
        Pod manifest as dict
    """
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "forge",
                "forge.openshift.io/project": "llm_d",
                "forge.openshift.io/component": "smoke",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "smoke",
                    "image": client_image,
                    "command": ["sleep", "infinity"],
                }
            ],
        },
    }
