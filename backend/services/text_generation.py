"""Shared text generation for the baseline and grounded chat."""
import config


def configured_provider() -> str | None:
    if config.LLM_MODEL.startswith("gemini"):
        return "Gemini" if config.GEMINI_API_KEY else None
    return "Groq" if config.GROQ_API_KEY else None


def generate_text(prompt: str, timeout_seconds: int, max_tokens: int, *, json_response: bool = False) -> str:
    provider = configured_provider()
    if provider == "Groq":
        import httpx

        request = {
            "model": config.LLM_MODEL,
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
        return response.json()["choices"][0]["message"]["content"]
    if provider == "Gemini":
        from google import genai

        generation_config = {"max_output_tokens": max_tokens}
        if json_response:
            generation_config["response_mime_type"] = "application/json"
            if config.LLM_MODEL == "gemini-3.5-flash":
                generation_config["thinking_config"] = {"thinking_level": "MINIMAL"}
        with genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options={"timeout": timeout_seconds * 1000},
        ) as client:
            return client.models.generate_content(
                model=config.LLM_MODEL,
                contents=prompt,
                config=generation_config,
            ).text or ""
    raise RuntimeError("No text model is configured.")
