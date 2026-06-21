"""
ChatCompletionsUser — Direct Chat Completions API baseline.

Sends POST /v1/chat/completions directly to inference. Used for A/B
comparison to isolate LlamaStack routing overhead.
"""

import os

from _common import get_next_index, load_prompts, validate_response
from locust import HttpUser, constant, task


class ChatCompletionsUser(HttpUser):
    wait_time = constant(0)
    abstract = True
    _prompts = None

    def on_start(self):
        self.model = os.environ.get("MODEL", "vllm-inference/llama-32-3b-instruct")
        self.input_tokens = int(os.environ.get("INPUT_TOKENS", "0"))
        self.output_tokens = int(os.environ.get("OUTPUT_TOKENS", "0"))
        self.default_prompt = os.environ.get("PROMPT", "What is the capital of France?")

        if self.input_tokens > 0 and ChatCompletionsUser._prompts is None:
            ChatCompletionsUser._prompts = load_prompts()

    @task
    def call_chat_completions(self):
        if ChatCompletionsUser._prompts:
            idx = get_next_index()
            prompt = ChatCompletionsUser._prompts[idx % len(ChatCompletionsUser._prompts)]
        else:
            prompt = self.default_prompt

        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        if self.output_tokens > 0:
            payload["max_tokens"] = self.output_tokens
            payload["stop"] = None
            payload["ignore_eos"] = True

        with self.client.post(
            "/v1/chat/completions", json=payload, name="chat-completions", catch_response=True
        ) as response:
            validate_response(response, expected_keys=("choices",))
