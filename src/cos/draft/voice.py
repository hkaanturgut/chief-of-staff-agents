"""Loading the operator's writing voice.

`config/voice.md` is prepended to the drafter's instructions. It matters more than the
prompt engineering around it: a draft that reads like a language model being helpful gets
rewritten, and then the system has saved nobody anything.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from cos.logging import get_logger
from cos.settings import CONFIG_DIR

log = get_logger("draft.voice")

VOICE_PATH = CONFIG_DIR / "voice.md"
PLACEHOLDER = "TODO(T076)"


@lru_cache(maxsize=1)
def load(path: Path | None = None) -> str:
    target = path or VOICE_PATH
    if not target.exists():
        log.warning("no voice file; drafts will sound generic", path=str(target))
        return ""
    content = target.read_text()
    if PLACEHOLDER in content:
        # Not fatal — the pipeline still works. But drafts in a generic voice are the
        # difference between a proposal the operator edits and one they rewrite.
        log.warning(
            "config/voice.md still contains placeholders; drafts will not sound like you",
            path=str(target),
        )
    return content
