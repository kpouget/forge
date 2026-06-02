"""
Utilities for the apply DataScienceCluster toolbox module.
"""

from __future__ import annotations

from typing import Any

import yaml

from projects.core.dsl import template


def render_datasciencecluster(
    *,
    datasciencecluster_name: str,
    namespace: str,
    components: list[str],
) -> dict[str, Any]:
    """Render a DataScienceCluster manifest from Jinja template.

    Args:
        datasciencecluster_name: Name of the DataScienceCluster
        namespace: Namespace for the DataScienceCluster
        components: List of components to enable

    Returns:
        DataScienceCluster manifest as dict
    """
    rendered_yaml = template.render_template(
        "datasciencecluster.yaml.j2",
        context={
            "datasciencecluster_name": datasciencecluster_name,
            "namespace": namespace,
            "components": components,
        },
    )
    return yaml.safe_load(rendered_yaml)
