"""Tests for Groq-to-Gemini retry and fallback in text_generation.py."""
from unittest.mock import patch

import config
from services.text_generation import generate_text


def test_groq_transient_retry_succeeds():
    calls = []

    def fake_groq(model, prompt, timeout_seconds, max_tokens, json_response):
        calls.append(model)
        if len(calls) == 1:
            raise TimeoutError("transient timeout")
        return "recovered-from-groq"

    with (
        patch.object(config, "LLM_MODEL", "qwen/qwen3.8-27b"),
        patch.object(config, "GROQ_API_KEY", "gsk_test"),
        patch.object(config, "GEMINI_API_KEY", "AIza_test"),
        patch("services.text_generation._call_groq", side_effect=fake_groq),
        patch("services.text_generation.time.sleep"),
    ):
        diag = {}
        out = generate_text("hello", 10, 50, diagnostics=diag)
    assert out == "recovered-from-groq"
    assert len(calls) == 2
    assert diag["provider_used"] == "Groq"
    assert diag["fallback_used"] is False


def test_groq_failure_falls_back_to_gemini():
    with (
        patch.object(config, "LLM_MODEL", "qwen/qwen3.8-27b"),
        patch.object(config, "GROQ_API_KEY", "gsk_test"),
        patch.object(config, "GEMINI_API_KEY", "AIza_test"),
        patch("services.text_generation._call_groq", side_effect=RuntimeError("groq down")),
        patch("services.text_generation._call_gemini", return_value="gemini-ok") as gem,
    ):
        diag = {}
        out = generate_text("hello", 10, 50, diagnostics=diag)
    assert out == "gemini-ok"
    gem.assert_called_once()
    assert diag["provider_used"] == "Gemini"
    assert diag["fallback_used"] is True

