"""
API Diagnostic Script - tests Gemini and Groq keys/models.
Run with:  $env:PYTHONPATH="backend"; .venv\Scripts\python.exe backend/data/test_apis.py
"""
import sys, os
# Ensure project root is on path so config.py finds the .env
_HERE = os.path.dirname(os.path.abspath(__file__))          # backend/data
_BACKEND = os.path.dirname(_HERE)                           # backend
_ROOT = os.path.dirname(_BACKEND)                           # project root
sys.path.insert(0, _BACKEND)

# Force-load .env before importing config
from dotenv import load_dotenv
for _p in [os.path.join(_ROOT, ".env"), os.path.join(_BACKEND, ".env")]:
    if os.path.exists(_p):
        load_dotenv(_p, override=True)
        print(f"[dotenv] Loaded {_p}")
        break

import config
from services.text_generation import configured_provider, generate_text

print(f"Configured provider: {configured_provider()}")
print(f"Configured model   : {config.LLM_MODEL}")

try:
    reply = generate_text("Summarize in 4 words: system is fully operational.", timeout_seconds=15, max_tokens=30)
    print(f"✅ Text generation working: {reply.strip()}")
except Exception as e:
    print(f"❌ Text generation failed : {e}")

print("=" * 60)
print("API DIAGNOSTIC SCRIPT")
print("=" * 60)

# ── Gemini ────────────────────────────────────────────────────
print("\n=== GEMINI ===")
key = getattr(config, "GEMINI_API_KEY", "")
print(f"Key prefix : {key[:12]}...")
print(f"Key length : {len(key)} chars")

GEMINI_MODELS = [
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]

try:
    from google import genai as _genai
    client = _genai.Client(api_key=key)
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents="Say hello in 3 words.",
                config={"max_output_tokens": 20},
            )
            print(f"  ✅ {model:<30} → {resp.text.strip()[:60]}")
        except Exception as e:
            print(f"  ❌ {model:<30} → {str(e)[:100]}")
except Exception as e:
    print(f"  Could not import google-genai: {e}")

# ── Groq ──────────────────────────────────────────────────────
print("\n=== GROQ ===")
groq_key = getattr(config, "GROQ_API_KEY", "")
print(f"Key prefix : {groq_key[:12]}...")
print(f"Key length : {len(groq_key)} chars")

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
]

try:
    import httpx
    for model in GROQ_MODELS:
        try:
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "Say hello in 3 words."}], "max_tokens": 20},
                timeout=15,
            )
            if r.status_code == 200:
                txt = r.json()["choices"][0]["message"]["content"].strip()[:60]
                print(f"  ✅ {model:<30} → {txt}")
            else:
                print(f"  ❌ {model:<30} → HTTP {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"  ❌ {model:<30} → {str(e)[:100]}")
except Exception as e:
    print(f"  Could not import httpx: {e}")

print("\n" + "=" * 60)
print("SUMMARY: check ✅ lines above — use a working model in .env")
print("=" * 60)
