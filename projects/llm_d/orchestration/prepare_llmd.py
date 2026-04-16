from __future__ import annotations

from projects.llm_d.toolbox.cleanup.main import run as cleanup_toolbox_run
from projects.llm_d.toolbox.prepare.main import run as prepare_toolbox_run


def prepare() -> int:
    return prepare_toolbox_run()


def cleanup() -> int:
    return cleanup_toolbox_run()
