"""
MCP Streamable HTTP Client for load testing.

Handles:
- JSON-RPC over HTTP POST to /mcp
- Session management via Mcp-Session-Id header
- SSE (Server-Sent Events) response parsing
- Optional Host header for gateway routing

Compatible with kubernetes-mcp-server and MCP Gateway broker.

Reference: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
"""

import json
import time
from dataclasses import dataclass, field

import requests


@dataclass
class MCPResponse:
    """Response from an MCP request."""

    success: bool
    response_time_ms: float
    status_code: int
    data: dict | None = None
    error: str | None = None


@dataclass
class MCPClient:
    """
    MCP Streamable HTTP client with session management.

    Supports both direct MCP server connections and gateway-routed connections.
    Handles JSON and SSE response formats from kubernetes-mcp-server.
    """

    base_url: str
    session_id: str | None = None
    host_header: str | None = None
    request_id: int = field(default=0, init=False)
    timeout: float = 30.0

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.host_header:
            h["Host"] = self.host_header
        return h

    def _parse_body(self, response, elapsed_ms: float) -> MCPResponse:
        """Parse HTTP response body, handling both JSON and SSE formats."""
        if not self.session_id and "Mcp-Session-Id" in response.headers:
            self.session_id = response.headers["Mcp-Session-Id"]

        if response.status_code != 200:
            return MCPResponse(
                success=False,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
            )

        try:
            ct = response.headers.get("Content-Type", "")
            text = response.text

            # SSE format: kubernetes-mcp-server returns event: message\ndata: {...}
            if "text/event-stream" in ct or text.lstrip().startswith("event:"):
                data = None
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        break
                if data is None:
                    return MCPResponse(
                        success=False,
                        response_time_ms=elapsed_ms,
                        status_code=response.status_code,
                        error="Empty SSE response",
                    )
            else:
                data = response.json()

            if not isinstance(data, dict):
                return MCPResponse(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=response.status_code,
                    error=f"Expected JSON object, got {type(data).__name__}",
                )

            if "error" in data:
                msg = data["error"]
                if isinstance(msg, dict):
                    msg = msg.get("message", str(msg))
                return MCPResponse(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=response.status_code,
                    error=str(msg),
                )

            return MCPResponse(
                success=True,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
                data=data.get("result"),
            )
        except json.JSONDecodeError as e:
            return MCPResponse(
                success=False,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
                error=f"Invalid JSON: {e}",
            )

    def _send(self, method: str, params: dict | None = None) -> MCPResponse:
        """Send a JSON-RPC request to the MCP server."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id(),
        }
        if params:
            payload["params"] = params

        start = time.perf_counter()
        try:
            resp = requests.post(
                f"{self.base_url}/mcp",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            elapsed = (time.perf_counter() - start) * 1000
            return self._parse_body(resp, elapsed)
        except requests.exceptions.RequestException as e:
            elapsed = (time.perf_counter() - start) * 1000
            return MCPResponse(False, elapsed, 0, error=str(e))

    def initialize(self) -> MCPResponse:
        """Initialize the MCP session. Must be called first."""
        return self._send(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"roots": {"listChanged": True}},
                "clientInfo": {"name": "locust-mcp", "version": "1.0.0"},
            },
        )

    def initialized_notification(self) -> MCPResponse:
        """Send initialized notification. No 'id' field per JSON-RPC 2.0 spec, expects HTTP 202."""
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        start = time.perf_counter()
        try:
            resp = requests.post(
                f"{self.base_url}/mcp",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            elapsed = (time.perf_counter() - start) * 1000
            return MCPResponse(
                success=resp.status_code in (200, 202, 204),
                response_time_ms=elapsed,
                status_code=resp.status_code,
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return MCPResponse(False, elapsed, 0, error=str(e))

    def list_tools(self) -> MCPResponse:
        """List available tools from the MCP server."""
        return self._send("tools/list")

    def call_tool(self, name: str, arguments: dict | None = None) -> MCPResponse:
        """Call a tool on the MCP server."""
        return self._send(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )

    def ping(self) -> MCPResponse:
        """Health check."""
        return self._send("ping")
