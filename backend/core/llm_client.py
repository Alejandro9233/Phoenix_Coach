"""
LLM Client — Abstraction layer over Groq API (cloud) and Ollama (local dev).

Uses the OpenAI-compatible SDK pointed at Groq's endpoint.
Falls back to Ollama if GROQ_API_KEY is not set (local dev mode).
"""
import os
import json
from openai import OpenAI

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# llama-3.3-70b-versatile was retired by Groq (404 as of 2026-08-17). Models
# must also be enabled for the org at console.groq.com/settings/limits — a
# listed-but-blocked model fails with model_permission_blocked_org.
GROQ_MODEL = os.getenv("COACHING_MODEL", "openai/gpt-oss-120b")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

def _use_groq() -> bool:
    """Return True if we should use Groq cloud, False for local Ollama."""
    return bool(GROQ_API_KEY)

def _get_groq_client() -> OpenAI:
    """Return an OpenAI client pointed at Groq's API."""
    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )

def chat_completion(messages: list[dict], json_mode: bool = False,
                    temperature: float = 0.7, seed: int | None = None) -> str:
    """
    Send a chat completion request to Groq (cloud) or Ollama (local).

    Args:
        messages: List of {"role": ..., "content": ...} dicts.
        json_mode: If True, request JSON output format.
        temperature: Sampling temperature. Plan generation and other
            structured-JSON calls run at 0.3, triage extraction at 0.2;
            the 0.7 default keeps free-form chat lively.
        seed: Sampling seed, passed to the provider when set.

    Returns:
        The assistant's response content as a string.

    What lower temperature + a fixed seed buys: markedly less run-to-run
    variance in workout selection, pace copying, and schema adherence — and a
    malformed-JSON retry that is likelier to reproduce a good structure. What
    it does NOT buy: identical plans across calls. The context text changes
    daily (date, recovery numbers), and provider-side nondeterminism survives
    seeding — Groq's OpenAI-compatible seed is explicitly best-effort, and
    gpt-oss-120b is MoE on batched LPU inference, so same-seed drift persists.
    "Plans still differ" is expected, not a bug.
    """
    if _use_groq():
        return _groq_chat(messages, json_mode, temperature, seed)
    else:
        return _ollama_chat(messages, json_mode, temperature, seed)

# JSON-mode budget (2026-08-24 incident). gpt-oss-120b is a reasoning model
# whose thinking tokens COUNT AGAINST the completion budget; with no explicit
# cap Groq allowed ~3072 completion tokens, the Wave-4 prompt pushed medium-
# effort reasoning to ~2800 of them, and the 7-day plan JSON truncated to
# nothing -> HTTP 400 json_validate_failed with an empty failed_generation,
# every single time. Two constraints fix it together:
# - reasoning_effort "low": measured 325 reasoning tokens on the same prompt,
#   full valid plan. Structured copy-the-menu output doesn't need deep
#   thought, and the volume/pace gates catch slippage structurally.
# - max_completion_tokens 5000: headroom above the ~3072 default WITHOUT
#   tripping the free tier's 8000 tokens-per-minute limiter, which counts
#   prompt + max_completion_tokens per request (30000 -> instant 413).
JSON_MODE_REASONING_EFFORT = "low"
JSON_MODE_MAX_COMPLETION_TOKENS = 5000


def _build_groq_kwargs(model: str, messages: list[dict], json_mode: bool,
                       temperature: float, seed: int | None,
                       reasoning_effort: str | None = None,
                       max_completion_tokens: int | None = None) -> dict:
    """Build the OpenAI-compatible request kwargs for a Groq chat call.

    Pure — tests verify the request shape here without a network call.
    json_mode defaults reasoning_effort/max_completion_tokens (see above);
    callers may override either explicitly.
    """
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if seed is not None:
        kwargs["seed"] = seed
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
        if reasoning_effort is None:
            reasoning_effort = JSON_MODE_REASONING_EFFORT
        if max_completion_tokens is None:
            max_completion_tokens = JSON_MODE_MAX_COMPLETION_TOKENS
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    if max_completion_tokens is not None:
        kwargs["max_completion_tokens"] = max_completion_tokens
    return kwargs

def _groq_chat(messages: list[dict], json_mode: bool,
               temperature: float, seed: int | None) -> str:
    """Call Groq via OpenAI-compatible SDK."""
    client = _get_groq_client()
    kwargs = _build_groq_kwargs(GROQ_MODEL, messages, json_mode, temperature, seed)

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content

def _build_ollama_options(temperature: float, seed: int | None) -> dict:
    """Shared options dict for Ollama chat and stream calls."""
    options = {"temperature": temperature}
    if seed is not None:
        options["seed"] = seed
    return options

def _ollama_chat(messages: list[dict], json_mode: bool,
                 temperature: float, seed: int | None) -> str:
    """Call local Ollama (for development/testing only)."""
    import ollama
    kwargs = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "options": _build_ollama_options(temperature, seed),
    }
    if json_mode:
        kwargs["format"] = "json"

    response = ollama.chat(**kwargs)
    content = response["message"]["content"]

    # Strip Qwen3 thinking tags
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    return content

async def chat_completion_stream(messages: list[dict],
                                 temperature: float = 0.7,
                                 seed: int | None = None):
    """
    Async generator that yields tokens for streaming responses.
    Used by the /chat SSE endpoint. Chat stays at 0.7 on purpose —
    conversation should remain lively; no caller lowers it.
    """
    if _use_groq():
        async for token in _groq_stream(messages, temperature, seed):
            yield token
    else:
        async for token in _ollama_stream(messages, temperature, seed):
            yield token

async def _groq_stream(messages: list[dict], temperature: float, seed: int | None):
    """Stream tokens from Groq."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    kwargs = _build_groq_kwargs(GROQ_MODEL, messages, False, temperature, seed)
    stream = await client.chat.completions.create(stream=True, **kwargs)
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

async def _ollama_stream(messages: list[dict], temperature: float, seed: int | None):
    """Stream tokens from local Ollama (dev mode)."""
    import ollama
    client = ollama.AsyncClient()
    inside_think = False
    think_buffer = ""

    async for chunk in await client.chat(
        model=OLLAMA_MODEL,
        messages=messages,
        stream=True,
        options=_build_ollama_options(temperature, seed),
    ):
        token = chunk["message"]["content"]
        if not token:
            continue

        # Handle <think> tag suppression (same logic as current main.py)
        if inside_think:
            think_buffer += token
            if "</think>" in think_buffer:
                after = think_buffer.split("</think>", 1)[1]
                inside_think = False
                think_buffer = ""
                if after.strip():
                    yield after
            continue

        if "<think>" in token:
            parts = token.split("<think>", 1)
            if parts[0]:
                yield parts[0]
            inside_think = True
            think_buffer = parts[1] if len(parts) > 1 else ""
            if "</think>" in think_buffer:
                after = think_buffer.split("</think>", 1)[1]
                inside_think = False
                think_buffer = ""
                if after.strip():
                    yield after
            continue

        yield token

def check_llm_available() -> dict:
    """Health check for the LLM backend. Returns status dict."""
    if _use_groq():
        try:
            client = _get_groq_client()
            # Minimal test call
            client.models.list()
            return {"provider": "groq", "status": "connected", "model": GROQ_MODEL}
        except Exception as e:
            return {"provider": "groq", "status": "error", "detail": str(e)}
    else:
        try:
            import ollama
            ollama.show(OLLAMA_MODEL)
            return {"provider": "ollama", "status": "connected", "model": OLLAMA_MODEL}
        except Exception:
            return {"provider": "ollama", "status": "disconnected", "model": OLLAMA_MODEL}
