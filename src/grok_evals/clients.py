"""Thin clients for xAI (OpenAI-compatible) and Anthropic, with retry."""
from __future__ import annotations

import os
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from ._paths import ROOT

load_dotenv(ROOT / ".env")


def _xai_key() -> str:
    k = os.environ.get("XAI_API_KEY") or os.environ.get("xAI_API_KEY")
    if not k:
        raise RuntimeError("XAI_API_KEY missing from environment / .env")
    return k


def _anthropic_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTRHOPIC_API_KEY")
    if not k:
        raise RuntimeError("ANTHROPIC_API_KEY missing from environment / .env")
    return k


def xai_client() -> OpenAI:
    return OpenAI(api_key=_xai_key(), base_url="https://api.x.ai/v1")


def anthropic_client() -> Anthropic:
    return Anthropic(api_key=_anthropic_key())


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30), reraise=True)
def xai_chat(messages: list[dict], model: str = "grok-4.3", **kw: Any) -> dict:
    resp = xai_client().chat.completions.create(model=model, messages=messages, **kw)
    return resp.model_dump()


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30), reraise=True)
def anthropic_message(
    messages: list[dict],
    model: str = "claude-sonnet-4-5-20250929",
    system: str | None = None,
    max_tokens: int = 2048,
    **kw: Any,
) -> dict:
    args: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, **kw}
    if system:
        args["system"] = system
    resp = anthropic_client().messages.create(**args)
    return resp.model_dump()
