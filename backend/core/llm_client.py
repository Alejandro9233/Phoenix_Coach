"""
LLM Client — Groq, through the OpenAI-compatible SDK.

Groq is the only provider. The local Ollama fallback was deleted 2026-11-13:
it never ran on the server, and a missing GROQ_API_KEY silently switching
models is worse than failing loud.
"""
import os
from openai import OpenAI

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# llama-3.3-70b-versatile was retired by Groq (404 as of 2026-08-17). Models
# must also be enabled for the org at console.groq.com/settings/limits — a
# listed-but-blocked model fails with model_permission_blocked_org.
GROQ_MODEL = os.getenv("COACHING_MODEL", "openai/gpt-oss-120b")

def _require_key():
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")

def _get_groq_client() -> OpenAI:
    """Return an OpenAI client pointed at Groq's API."""
    _require_key()
    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )

def chat_completion(messages: list[dict], json_mode: bool = False,
                    temperature: float = 0.7, seed: int | None = None) -> str:
    """
    Send a chat completion request to Groq.

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
    return _groq_chat(messages, json_mode, temperature, seed)

# JSON-mode budget (2026-08-24 incident). gpt-oss-120b is a reasoning model
# whose thinking tokens COUNT AGAINST the completion budget; with no explicit
# cap Groq allowed ~3072 completion tokens, the Wave-4 prompt pushed medium-
# effort reasoning to ~2800 of them, and the 7-day plan JSON truncated to
# nothing -> HTTP 400 json_validate_failed with an empty failed_generation,
# every single time. Two constraints fix it together:
# - reasoning_effort "low": measured 325 reasoning tokens on the same prompt,
#   full valid plan. Structured copy-the-menu output doesn't need deep
#   thought, and the volume/pace gates catch slippage structurally.
# - max_completion_tokens 4000: headroom above the ~3072 default WITHOUT
#   tripping the free tier's 8000 tokens-per-minute limiter, which admits a
#   request by prompt + max_completion_tokens (30000 -> instant 413). The
#   REAL plan prompt measured 3145 tokens on 2026-08-24, so 5000 overflowed
#   by 145; 4000 leaves ~850 margin while a plan needs ~2500 (JSON ~2100 +
#   low reasoning ~350). If the prompt grows past ~3900 tokens the 413
#   returns — trim the prompt, not this cap.
JSON_MODE_REASONING_EFFORT = "low"
JSON_MODE_MAX_COMPLETION_TOKENS = 4000

# A second JSON call in the same minute (volume-gate retry, resample) can't
# be admitted until the TPM window frees: first call consumes ~5600 of 8000.
# One blocking wait is acceptable on the plan paths (iOS waits 180s) and
# only there — never in chat streaming.
TPM_RETRY_WAIT_SEC = 45


def _is_tpm_limit_error(exc: Exception) -> bool:
    text = str(exc)
    return "rate_limit_exceeded" in text and ("TPM" in text or "tokens" in text)


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
    """Call Groq via OpenAI-compatible SDK.

    JSON-mode calls wait out one TPM rejection: the free tier's per-minute
    budget can't admit two plan-sized requests back to back, and the second
    request is usually a gate retry that deserves to run.
    """
    import time

    client = _get_groq_client()
    kwargs = _build_groq_kwargs(GROQ_MODEL, messages, json_mode, temperature, seed)

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        if not (json_mode and _is_tpm_limit_error(e)):
            raise
        print(f"⏳ Groq TPM window full; waiting {TPM_RETRY_WAIT_SEC}s: {e}")
        time.sleep(TPM_RETRY_WAIT_SEC)
        response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content

async def chat_completion_stream(messages: list[dict],
                                 temperature: float = 0.7,
                                 seed: int | None = None):
    """
    Async generator that yields tokens for streaming responses.
    Used by the /chat SSE endpoint. Chat stays at 0.7 on purpose —
    conversation should remain lively; no caller lowers it.
    """
    async for token in _groq_stream(messages, temperature, seed):
        yield token

async def _groq_stream(messages: list[dict], temperature: float, seed: int | None):
    """Stream tokens from Groq."""
    from openai import AsyncOpenAI
    _require_key()
    client = AsyncOpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    kwargs = _build_groq_kwargs(GROQ_MODEL, messages, False, temperature, seed)
    stream = await client.chat.completions.create(stream=True, **kwargs)
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

def check_llm_available() -> dict:
    """Health check for the LLM backend. Returns status dict."""
    try:
        client = _get_groq_client()
        # Minimal test call
        client.models.list()
        return {"provider": "groq", "status": "connected", "model": GROQ_MODEL}
    except Exception as e:
        return {"provider": "groq", "status": "error", "detail": str(e)}
