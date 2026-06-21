"""
ResponsesMCPBenchmarkUser — Responses API with deterministic tokenized MCP server.

Uses the tokenizer-accurate MCP Server which returns exactly
TOOL_RESPONSE_TOKENS tokens per tool call. Eliminates noise from real
MCP servers for controlled overhead measurement.
"""

import os
import random

from _common import load_prompts, validate_response
from locust import HttpUser, constant, task


class ResponsesMCPBenchmarkUser(HttpUser):
    wait_time = constant(0)
    abstract = True
    _prompts = None

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/qwen3-vl-30b-a3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        self.mcp_server = os.environ.get(
            "MCP_SERVER", "http://benchmark-mcp-server.llamastack-bench.svc.cluster.local:8000/sse"
        )
        self.default_prompt = os.environ.get(
            "PROMPT", "Use tool_0 to retrieve a document and summarize it."
        )

        if self.input_tokens > 0 and ResponsesMCPBenchmarkUser._prompts is None:
            ResponsesMCPBenchmarkUser._prompts = load_prompts()

    @task
    def call_responses_mcp_benchmark(self):
        if ResponsesMCPBenchmarkUser._prompts:
            prompt = random.choice(ResponsesMCPBenchmarkUser._prompts)
        else:
            prompt = self.default_prompt

        payload = {
            "model": self.model,
            "input": prompt,
            "stream": False,
            "tools": [
                {
                    "type": "mcp",
                    "server_label": "benchmark",
                    "server_url": self.mcp_server,
                }
            ],
        }
        if self.output_tokens > 0:
            payload["max_output_tokens"] = self.output_tokens

        with self.client.post(
            "/v1/responses", json=payload, name="responses-mcp-benchmark", catch_response=True
        ) as response:
            validate_response(response)
