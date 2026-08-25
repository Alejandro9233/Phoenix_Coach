"""C5 — determinism knobs on LLM calls.

chat_completion carries temperature and seed to the provider. Plan generation
and other structured-JSON calls run at 0.3 with a fixed seed, triage
extraction at 0.2, free-form chat keeps the 0.7 default. Seeding is
best-effort at the provider, so these tests pin the REQUEST shape — never
output determinism ("plans still differ" is expected, not a bug).
"""
from backend.core.llm_client import (
    JSON_MODE_MAX_COMPLETION_TOKENS,
    JSON_MODE_REASONING_EFFORT,
    _build_groq_kwargs,
)
from backend.agents.response_agent import (
    PLAN_SEED,
    PLAN_TEMPERATURE,
    ResponseAgent,
)
from backend.services.issue_triage import TRIAGE_TEMPERATURE, extract_travel

MESSAGES = [{"role": "user", "content": "hi"}]


# ─── request-shape helper (pure, no network) ─────────────────────────────────

def test_build_groq_kwargs_includes_temperature_and_seed():
    kwargs = _build_groq_kwargs(
        "some-model", MESSAGES, json_mode=False, temperature=0.3, seed=1042
    )
    assert kwargs["temperature"] == 0.3
    assert kwargs["seed"] == 1042
    assert kwargs["model"] == "some-model"
    assert kwargs["messages"] is MESSAGES
    assert "response_format" not in kwargs


def test_build_groq_kwargs_omits_seed_when_none():
    kwargs = _build_groq_kwargs(
        "some-model", MESSAGES, json_mode=False, temperature=0.7, seed=None
    )
    assert "seed" not in kwargs
    assert kwargs["temperature"] == 0.7


def test_build_groq_kwargs_keeps_response_format_under_json_mode():
    kwargs = _build_groq_kwargs(
        "some-model", MESSAGES, json_mode=True, temperature=0.3, seed=1042
    )
    assert kwargs["response_format"] == {"type": "json_object"}


def test_json_mode_bounds_reasoning_and_completion():
    """2026-08-24 incident: with no bounds, gpt-oss-120b's reasoning ate the
    ~3072-token default completion budget and every plan generation 400'd
    with json_validate_failed. json_mode must default to low reasoning
    effort and a TPM-safe completion cap (free tier counts prompt +
    max_completion_tokens against 8000 TPM — a big cap is an instant 413)."""
    kwargs = _build_groq_kwargs(
        "some-model", MESSAGES, json_mode=True, temperature=0.3, seed=1042
    )
    assert kwargs["reasoning_effort"] == JSON_MODE_REASONING_EFFORT == "low"
    assert kwargs["max_completion_tokens"] == JSON_MODE_MAX_COMPLETION_TOKENS == 5000

    # Callers may override; explicit values win over the defaults.
    kwargs = _build_groq_kwargs(
        "some-model", MESSAGES, json_mode=True, temperature=0.3, seed=1042,
        reasoning_effort="medium", max_completion_tokens=4096,
    )
    assert kwargs["reasoning_effort"] == "medium"
    assert kwargs["max_completion_tokens"] == 4096

    # Non-JSON calls (chat) get neither — conversation keeps full reasoning.
    kwargs = _build_groq_kwargs(
        "some-model", MESSAGES, json_mode=False, temperature=0.7, seed=None
    )
    assert "reasoning_effort" not in kwargs
    assert "max_completion_tokens" not in kwargs


def test_json_validate_failed_retries_once(monkeypatch):
    """Groq's server-side JSON rejection gets exactly one resample; other
    API errors still propagate immediately."""
    import backend.agents.response_agent as ra

    calls = []

    def flaky(messages, json_mode=False, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise Exception(
                "Error code: 400 - {'error': {'code': 'json_validate_failed'}}"
            )
        return '{"ok": true}'

    monkeypatch.setattr(ra, "chat_completion", flaky)
    agent = ResponseAgent()
    assert agent._chat_json_with_retry(MESSAGES) == {"ok": True}
    assert len(calls) == 2

    def dead(messages, json_mode=False, **kwargs):
        raise Exception("Error code: 404 - model_not_found")

    monkeypatch.setattr(ra, "chat_completion", dead)
    try:
        agent._chat_json_with_retry(MESSAGES)
        assert False, "non-JSON API errors must propagate"
    except Exception as e:
        assert "model_not_found" in str(e)


# ─── call sites request the right params ─────────────────────────────────────

def test_weekly_plan_requests_cold_seeded_completion(monkeypatch):
    import backend.agents.response_agent as ra

    captured = {}

    def fake(messages, json_mode=False, **kwargs):
        captured["json_mode"] = json_mode
        captured.update(kwargs)
        return '{"week_summary": {"focus": "ok"}, "days": {}}'

    monkeypatch.setattr(ra, "chat_completion", fake)

    ResponseAgent().generate_weekly_plan(
        "summary", {}, training_context={"phase_name": "Foundation"}
    )

    assert captured["json_mode"] is True
    assert captured["temperature"] == PLAN_TEMPERATURE == 0.3
    assert captured["seed"] == PLAN_SEED == 1042


def test_travel_extraction_requests_low_temperature(monkeypatch):
    captured = {}

    def fake(messages, json_mode=False, **kwargs):
        captured.update(kwargs)
        return '{"is_travel": false}'

    # issue_triage imports chat_completion inside the function, so the patch
    # must land on the source module.
    monkeypatch.setattr("backend.core.llm_client.chat_completion", fake)

    assert extract_travel("traveling friday") is None
    assert captured["temperature"] == TRIAGE_TEMPERATURE == 0.2
