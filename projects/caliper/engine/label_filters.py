"""Include/exclude filters on distinguishing labels."""

from __future__ import annotations

from typing import Any

LabelMap = dict[str, Any]


def parse_filter_kv(pairs: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"Invalid filter (expected KEY=VALUE): {p}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def matches_filters(
    labels: LabelMap,
    *,
    include: dict[str, str],
    exclude: dict[str, str],
) -> bool:
    """Exclude wins on conflict; include requires all pairs to match when non-empty."""
    for k, v in exclude.items():
        if labels.get(k) == v:
            return False
    if not include:
        return True
    return all(labels.get(k) == v for k, v in include.items())


def filter_records(
    records: list[Any],
    *,
    include: dict[str, str],
    exclude: dict[str, str],
) -> list[Any]:
    out: list[Any] = []
    for r in records:
        if matches_filters(
            getattr(r, "distinguishing_labels", {}),
            include=include,
            exclude=exclude,
        ):
            out.append(r)
    return out
