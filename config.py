"""
Top‑level configuration alias for Phase 1.

The test suite expects a module named `config` in the project root that provides the same variables as
`backend/config.py`. To keep a single source of truth we simply re‑export everything from the backend module.
"""

from pathlib import Path
import os

# Import all symbols from the backend config module.
# This mirrors the definitions used throughout the codebase.
from backend.config import (
    GEMINI_API_KEY,
    GROQ_API_KEY,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    LLM_MODEL,
    VISION_MODEL,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    OLLAMA_BASE_URL,
    LOG_LEVEL,
    DATA_DIR,
    DEMO_DIR,
    LOG_CHUNK_SIZE,
    LOG_CHUNK_OVERLAP,
)

# Additionally expose a convenient path to the project root for any external scripts.
PROJECT_ROOT: Path = Path(__file__).parent

