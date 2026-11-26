"""Shared text generation with retry and automatic provider fallback."""
import logging
import re
import time

import config

logger = logging.getLogger("logmind.text_generation")

FALLBACK_GROQ_MODEL = "qwen/qwen3.8-27b"
FALLBACK_GEMINI_MODEL = "gemini-2.5-flash"
_TRANSIENT_STATUS_CODES = {429, 502, 503, 504}


def configured_provider() -> str | None:
    if config.LLM_MODEL.startswith("gemini"):
        if config.GEMINI_API_KEY:
            return "Gemini"
        return "Groq" if config.GROQ_API_KEY else None
    if config.GROQ_API_KEY:
        return "Groq"
    return "Gemini" if config.GEMINI_API_KEY else None


def _provider_chain() -> list[tuple[str, str]]:
    """Return ordered [(provider_name, model_name)] pairs based on configured keys."""
    chain: list[tuple[str, str]] = []
    if config.LLM_MODEL.startswith("gemini"):
        if config.GEMINI_API_KEY:
            chain.append(("Gemini", config.LLM_MODEL))
        if config.GROQ_API_KEY:
            chain.append(("Groq", FALLBACK_GROQ_MODEL))
    else:
        if config.GROQ_API_KEY:
            chain.append(("Groq", config.LLM_MODEL))
        if config.GEMINI_API_KEY:
            chain.append(("Gemini", FALLBACK_GEMINI_MODEL))
    return chain


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return True
    code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(code, int) and code in _TRANSIENT_STATUS_CODES


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", cleaned, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else cleaned


def _call_groq(model: str, prompt: str, timeout_seconds: int, max_tokens: int, json_response: bool) -> str:
    import httpx

    request = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if json_response:
        request["response_format"] = {"type": "json_object"}
    response = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        json=request,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def _call_gemini(model: str, prompt: str, timeout_seconds: int, max_tokens: int, json_response: bool) -> str:
    from google import genai

    generation_config = {"max_output_tokens": max_tokens}
    if json_response:
        generation_config["response_mime_type"] = "application/json"
        if model == "gemini-3.5-flash":
            generation_config["thinking_config"] = {"thinking_level": "MINIMAL"}

    with genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options={"timeout": timeout_seconds * 1000},
    ) as client:
        try:
            text = client.models.generate_content(
                model=model,
                contents=prompt,
                config=generation_config,
            ).text or ""
        except Exception as exc:
            code = getattr(exc, "code", None)
            if json_response and code == 400:
                # Some Gemini models reject response_mime_type on complex prompts; retry without it.
                fallback_cfg = {"max_output_tokens": max_tokens}
                text = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=fallback_cfg,
                ).text or ""
            else:
                raise
    return _strip_json_fences(text) if json_response else text


def generate_text(
    prompt: str,
    timeout_seconds: int,
    max_tokens: int,
    *,
    json_response: bool = False,
    diagnostics: dict | None = None,
) -> str:
    chain = _provider_chain()
    if not chain:
        raise RuntimeError("No text model is configured.")

    last_exc: Exception | None = None
    for index, (provider, model) in enumerate(chain):
        caller = _call_groq if provider == "Groq" else _call_gemini
        attempts = 2 if index == 0 else 1
        for attempt in range(attempts):
            try:
                text = caller(model, prompt, timeout_seconds, max_tokens, json_response)
                if diagnostics is not None:
                    diagnostics["provider_used"] = provider
                    diagnostics["model_used"] = model
                    diagnostics["fallback_used"] = index > 0
                return text
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < attempts and _is_transient(exc):
                    logger.info("%s (%s) transient error (%s); retrying once.", provider, model, type(exc).__name__)
                    time.sleep(0.5)
                    continue
                if index + 1 < len(chain):
                    logger.warning("%s (%s) failed (%s); falling back to %s.", provider, model, type(exc).__name__, chain[index + 1][0])
                break
    assert last_exc is not None
    raise last_exc
