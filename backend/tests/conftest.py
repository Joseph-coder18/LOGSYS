"""
conftest.py – make pytest add `backend/` to sys.path so all service modules
are importable when running from the repo root or from backend/tests/.
"""
import sys
from pathlib import Path

# Insert backend/ at the front of sys.path
backend_dir = Path(__file__).parent.parent  # backend/
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

