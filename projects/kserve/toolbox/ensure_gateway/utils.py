"""
Utilities for the ensure gateway toolbox module.
"""

from __future__ import annotations

from typing import Any

import yaml

from projects.core.dsl import template


def render_gateway(
    *,
    name: str,
    namespace: str,
    gateway_class_name: str,
) -> dict[str, Any]:
    """Render a Gateway manifest from Jinja template.

    Args:
        name: Gateway name
        namespace: Gateway namespace
        gateway_class_name: Gateway class name

    Returns:
        Gateway manifest as dict
    """
    rendered_yaml = template.render_template(
        "gateway.yaml.j2",
        context={
            "name": name,
            "namespace": namespace,
            "gateway_class_name": gateway_class_name,
        },
    )
    return yaml.safe_load(rendered_yaml)
