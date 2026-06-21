"""
ResponsesSimpleUser — Responses API without tools (overhead measurement).

Sends POST /v1/responses with a prompt and collects latency/throughput.
When INPUT_TOKENS > 0, uses unique tokenizer-accurate synthetic prompts
to defeat vLLM prefix-cache. When OUTPUT_TOKENS > 0, forces exact
output length via max_output_tokens + ignore_eos.
"""

import os

from _common import get_next_index, load_prompts, validate_response
from locust import HttpUser, constant, task


class ResponsesSimpleUser(HttpUser):
    wait_time = constant(0)
    abstract = True
    _prompts = None

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        self.default_prompt = os.environ.get("PROMPT", "What is the capital of France?")

        if self.input_tokens > 0 and ResponsesSimpleUser._prompts is None:
            ResponsesSimpleUser._prompts = load_prompts()

    @task
    def call_responses_simple(self):
        if ResponsesSimpleUser._prompts:
            idx = get_next_index()
            prompt = ResponsesSimpleUser._prompts[idx % len(ResponsesSimpleUser._prompts)]
        else:
            prompt = self.default_prompt

        payload = {
            "model": self.model,
            "input": prompt,
            "stream": False,
        }
        if self.output_tokens > 0:
            payload["max_output_tokens"] = self.output_tokens
            payload["ignore_eos"] = True
            payload["stop_token_ids"] = []

        with self.client.post(
            "/v1/responses", json=payload, name="responses-simple", catch_response=True
        ) as response:
            validate_response(response)
