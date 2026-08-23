"""The committed schemas must match what the models currently produce.

Without this, a field added to a Pydantic model silently stops being reflected in the
schema the agent is constrained to, and the two drift until something malformed slips
through.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from export_schemas import main  # noqa: E402


def test_committed_schemas_are_current() -> None:
    assert main(check=True) == 0, (
        "committed JSON Schemas are stale; run `uv run python scripts/export_schemas.py`"
    )
