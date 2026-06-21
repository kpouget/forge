"""
Tokenizer-accurate text generation utility.

Generates text of EXACTLY N tokens using an encode-trim-decode loop.
Used by both:
- Benchmark MCP Server (tool responses of exact token count)
- Synthetic prompt generator (input prompts of exact token count)

Algorithm (from openshift-psap/llamastack-performance MOCK_MCP):
1. Sample random token IDs from vocabulary (excluding specials)
2. Decode to text
3. Encode back and check length
4. If not exact, trim token IDs to N and decode again
5. Repeat until convergence (typically 1-3 iterations)
"""

from __future__ import annotations

import random
from typing import Any


def get_valid_token_ids(tokenizer: Any) -> list[int]:
    """Get non-special token IDs from a HuggingFace tokenizer vocabulary."""
    vocab = tokenizer.get_vocab()
    special_ids = set()
    for attr in ("bos_token_id", "eos_token_id", "pad_token_id", "unk_token_id"):
        tid = getattr(tokenizer, attr, None)
        if tid is not None:
            special_ids.add(tid)
    if hasattr(tokenizer, "all_special_ids"):
        special_ids.update(tokenizer.all_special_ids)
    return [tid for tid in vocab.values() if tid not in special_ids]


def build_exact_text(
    tokenizer: Any,
    valid_ids: list[int],
    num_tokens: int,
    prefix: str = "",
    max_attempts: int = 10,
) -> str:
    """Build text of exactly `num_tokens` tokens using encode-trim-decode loop.

    Args:
        tokenizer: HuggingFace tokenizer instance
        valid_ids: List of non-special token IDs to sample from
        num_tokens: Exact target token count
        prefix: Optional text prefix (included in token count)
        max_attempts: Number of fresh random samples to try

    Returns:
        Text string that tokenizes to exactly num_tokens tokens.
        Falls back to best-effort if convergence fails after all attempts.
    """
    for _attempt in range(max_attempts):
        filler_ids = random.choices(valid_ids, k=num_tokens * 2)
        text = prefix + tokenizer.decode(filler_ids, skip_special_tokens=True)

        for _ in range(10):
            ids = tokenizer.encode(text, add_special_tokens=False)
            if len(ids) == num_tokens:
                return text
            text = tokenizer.decode(ids[:num_tokens], skip_special_tokens=True)

    return text


def build_text_pool(
    tokenizer: Any,
    valid_ids: list[int],
    num_tokens: int,
    pool_size: int,
    prefix: str = "",
) -> list[str]:
    """Pre-generate a pool of random text entries, each exactly `num_tokens` tokens."""
    return [
        build_exact_text(tokenizer, valid_ids, num_tokens, prefix=prefix) for _ in range(pool_size)
    ]
