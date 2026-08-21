"""C5 — determinism knobs on LLM calls.

chat_completion carries temperature and seed to the provider. Plan generation
and other structured-JSON calls run at 0.3 with a fixed seed, triage
extraction at 0.2, free-form chat keeps the 0.7 default. Seeding is
best-effort at the provider, so these tests pin the REQUEST shape — never
output determinism ("plans still differ" is expected, not a bug).
"""
from backend.core.llm_client import _build_groq_kwargs
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
