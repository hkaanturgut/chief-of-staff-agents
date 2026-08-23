"""Structured logging.

Two renderers. The terminal gets something a human reads while a demo is running; the
run log gets JSON lines, because the manifest and the cost figures in the pull request
are derived from them.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog

from cos.settings import REPO_ROOT

RUNS_DIR = REPO_ROOT / "state" / "runs"


def configure(*, verbose: bool = False, json_output: bool = False) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if verbose else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


def run_log_path(run_id: str) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR / f"{run_id}.jsonl"
