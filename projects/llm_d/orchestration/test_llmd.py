from __future__ import annotations

from projects.llm_d.orchestration import llmd_runtime
from projects.llm_d.toolbox.test.main import resolve_endpoint_url
from projects.llm_d.toolbox.test.main import run as test_toolbox_run
from projects.llm_d.toolbox.test.main import run_test


def init() -> None:
    llmd_runtime.init()


def test() -> int:
    return test_toolbox_run()
