"""Dictionary utilities with dot notation access."""

from __future__ import annotations

from typing import Any


def get_nested(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Get a nested dictionary value using dot notation.

    Args:
        data: Dictionary to search (not modified)
        key: Dot-separated key path (e.g., 'a.b.c')
        default: Default value if key doesn't exist

    Returns:
        Value at the key path, or default if not found

    Example:
        data = {'a': {'b': {'c': 1}}}
        value = get_nested(data, 'a.b.c')  # Returns 1
        value = get_nested(data, 'missing.key', 'default')  # Returns 'default'
    """
    try:
        keys = key.split(".")
        current = data
        for k in keys:
            current = current[k]
        return current
    except (KeyError, TypeError, AttributeError):
        return default


def set_nested(data: dict[str, Any], key: str, value: Any) -> None:
    """Set a nested dictionary value using dot notation.

    Creates intermediate dictionaries if they don't exist.

    Args:
        data: Dictionary to modify (modified in-place)
        key: Dot-separated key path (e.g., 'a.b.c')
        value: Value to set

    Example:
        data = {}
        set_nested(data, 'a.b.c', 1)  # Creates {'a': {'b': {'c': 1}}}
        set_nested(data, 'a.b.d', 2)  # Now {'a': {'b': {'c': 1, 'd': 2}}}
    """
    keys = key.split(".")
    current = data

    # Navigate to the parent of the target key, creating dicts as needed
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        elif not isinstance(current[k], dict):
            raise ValueError(f"Cannot set key '{key}': '{k}' is not a dictionary")
        current = current[k]

    # Set the final key
    current[keys[-1]] = value
