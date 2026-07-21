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
GROQ_MODEL = os.getenv("COACHING_MODEL", "llama-3.3-70b-versatile")
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

def chat_completion(messages: list[dict], json_mode: bool = False) -> str:
    """
    Send a chat completion request to Groq (cloud) or Ollama (local).

    Args:
        messages: List of {"role": ..., "content": ...} dicts.
        json_mode: If True, request JSON output format.

    Returns:
        The assistant's response content as a string.
    """
    if _use_groq():
        return _groq_chat(messages, json_mode)
    else:
        return _ollama_chat(messages, json_mode)

def _groq_chat(messages: list[dict], json_mode: bool) -> str:
    """Call Groq via OpenAI-compatible SDK."""
    client = _get_groq_client()
    kwargs = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.7,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content

def _ollama_chat(messages: list[dict], json_mode: bool) -> str:
    """Call local Ollama (for development/testing only)."""
    import ollama
    kwargs = {
        "model": OLLAMA_MODEL,
        "messages": messages,
    }
    if json_mode:
        kwargs["format"] = "json"

    response = ollama.chat(**kwargs)
    content = response["message"]["content"]

    # Strip Qwen3 thinking tags
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    return content

async def chat_completion_stream(messages: list[dict]):
    """
    Async generator that yields tokens for streaming responses.
    Used by the /chat SSE endpoint.
    """
    if _use_groq():
        async for token in _groq_stream(messages):
            yield token
    else:
        async for token in _ollama_stream(messages):
            yield token

async def _groq_stream(messages: list[dict]):
    """Stream tokens from Groq."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    stream = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        stream=True,
        temperature=0.7,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

async def _ollama_stream(messages: list[dict]):
    """Stream tokens from local Ollama (dev mode)."""
    import ollama
    client = ollama.AsyncClient()
    inside_think = False
    think_buffer = ""

    async for chunk in await client.chat(
        model=OLLAMA_MODEL,
        messages=messages,
        stream=True,
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
