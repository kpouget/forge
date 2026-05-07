"""Default regression rules (threshold relative delta)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RegressionRule:
    """Simple relative threshold: flag if change exceeds fraction."""

    max_relative_regression: float = 0.1  # 10% worse


DEFAULT_RULE = RegressionRule()
