"""
Shared MCP Session User for Locust load testing.

Deterministic round-robin MCP session user that cycles through tool calls.
Reusable across any FORGE project that benchmarks MCP servers or gateways.

Environment variables (set by the Locust job template):
    TOOL_PREFIX:         prefix for tool names ("mock_" via gateway, "" direct)
    HOST_HEADER:         Host header for gateway routing (empty = none)
    CALLS_PER_SESSION:   tool calls per session (0 = infinite)
    NUM_SERVERS:         number of registered servers for scale-out mode (0 = single server)
"""

import logging
import os
import random
import time

from locust import User, between, events, task
from mcp_client import MCPClient, MCPResponse

logger = logging.getLogger(__name__)

# ── configuration ──────────────────────────────────────────────────────────────

TOOL_PREFIX = os.environ.get("TOOL_PREFIX", "")
HOST_HEADER = os.environ.get("HOST_HEADER", "")
CALLS_PER_SESSION = int(os.environ.get("CALLS_PER_SESSION", "0"))
NUM_SERVERS = int(os.environ.get("NUM_SERVERS", "0"))

# ── deterministic tool sequence ───────────────────────────────────────────────

TOOL_SEQUENCE = [
    {"name": "alpha", "args": {"input": "test"}},
    {"name": "bravo", "args": {"input": "test"}},
    {"name": "charlie", "args": {"input": "test"}},
    {"name": "delta", "args": {"input": "test"}},
    {"name": "echo", "args": {"input": "test"}},
    {"name": "foxtrot", "args": {"input": "test"}},
    {"name": "golf", "args": {"input": "test"}},
    {"name": "hotel", "args": {"input": "test"}},
    {"name": "india", "args": {"input": "test"}},
    {"name": "juliet", "args": {"input": "test"}},
]

# ── locust reporting ──────────────────────────────────────────────────────────


def _report(name: str, resp: MCPResponse):
    """Fire a Locust request event for statistics tracking."""
    if resp.success:
        events.request.fire(
            request_type="MCP",
            name=name,
            response_time=resp.response_time_ms,
            response_length=len(str(resp.data)) if resp.data else 0,
            exception=None,
            context={},
        )
    else:
        events.request.fire(
            request_type="MCP",
            name=f"FAIL:{name}",
            response_time=resp.response_time_ms,
            response_length=0,
            exception=Exception(resp.error),
            context={},
        )


# ── user class ────────────────────────────────────────────────────────────────


class MCPSessionUser(User):
    """
    Deterministic round-robin MCP session user for mock server.

    Each user cycles through TOOL_SEQUENCE in order:
        tool[0], tool[1], ..., tool[9], tool[0], tool[1], ...
    """

    abstract = True
    wait_time = between(0.1, 0.5)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp: MCPClient | None = None
        self.calls_done = 0
        self.seq_index = 0

    def _open_session(self) -> bool:
        """Start a new MCP session. Returns True on success."""
        host_header = HOST_HEADER or None
        if NUM_SERVERS > 0:
            server_idx = random.randint(1, NUM_SERVERS)
            host_header = f"server{server_idx}.mcp.local"
            self._current_server_idx = server_idx
        else:
            self._current_server_idx = 0

        self.mcp = MCPClient(
            base_url=self.host,
            host_header=host_header,
        )

        r = self.mcp.initialize()
        _report("initialize", r)
        if not r.success:
            self.mcp = None
            return False

        self.mcp.initialized_notification()

        r = self.mcp.list_tools()
        _report("tools/list", r)
        if not r.success:
            self.mcp = None
            return False

        self.calls_done = 0
        return True

    def _close_session(self):
        """Close current session."""
        self.mcp = None
        self.calls_done = 0

    @task
    def do_tool_call(self):
        """Execute next tool in the deterministic sequence."""

        # open session if needed
        if self.mcp is None:
            if not self._open_session():
                time.sleep(1)
                return

        # session restart check
        if CALLS_PER_SESSION > 0 and self.calls_done >= CALLS_PER_SESSION:
            self._close_session()
            if not self._open_session():
                time.sleep(1)
                return

        entry = TOOL_SEQUENCE[self.seq_index % len(TOOL_SEQUENCE)]
        self.seq_index += 1

        if NUM_SERVERS > 0 and self._current_server_idx > 0:
            prefix = f"server{self._current_server_idx}_"
        else:
            prefix = TOOL_PREFIX
        tool_name = f"{prefix}{entry['name']}"
        args = dict(entry["args"])

        r = self.mcp.call_tool(tool_name, args)
        _report(f"call:{entry['name']}", r)

        self.calls_done += 1
