"""
ResponsesMCPUser — Responses API with MCP tool calling (full agentic flow).

Sends POST /v1/responses with tools=[{type: "mcp", server_url: ...}].
The inference stack mediates the MCP tool calls (list_tools + call_tool).
"""

import os

from _common import validate_response
from locust import HttpUser, constant, task


class ResponsesMCPUser(HttpUser):
    wait_time = constant(0)
    abstract = True

    def on_start(self):
        self.mcp_server = os.environ.get(
            "MCP_SERVER", "http://sdg-docs-mcp-server.llamastack.svc.cluster.local:8000/sse"
        )
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.prompt = os.environ.get("PROMPT", "What is Kubernetes?")

    @task
    def call_responses_with_mcp(self):
        payload = {
            "model": self.model,
            "input": self.prompt,
            "stream": False,
            "tools": [
                {
                    "type": "mcp",
                    "server_label": "deepwiki",
                    "server_url": self.mcp_server,
                    "require_approval": "never",
                }
            ],
        }

        with self.client.post(
            "/v1/responses", json=payload, name="responses-mcp", catch_response=True
        ) as response:
            validate_response(response)
