#!/usr/bin/env python
"""Export the JSON Schemas the agents are constrained to.

Generated from the Pydantic models so the schema an agent is held to and the schema the
response is validated against can never drift apart. `tests/test_schemas_current.py`
asserts the committed files still match, so a model change that is not reflected here
fails CI instead of drifting silently.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cos.models import Contract, Ledger, ProposedAction, Signal

CONTRACTS = (
    Path(__file__).resolve().parents[1] / "specs" / "001-chief-of-staff-pipeline" / "contracts"
)


class SignalExtraction(Contract):
    """What an ingest agent returns. Extraction only — no ranking, no drafting."""

    signals: list[Signal] = Field(default_factory=list)


class ClusterMerge(BaseModel):
    """What the consolidator's Stage B returns, for one candidate cluster.

    Deliberately narrow. It has no `urgency`, `due`, `sources`, or `id` field:

    - `urgency` is arithmetic in consolidate/rank.py (Constitution III).
    - `due` is carried forward from the merged signals, each of which took it from an
      explicit statement in a source. Letting the merge step restate a date is how an
      inferred deadline sneaks back in through the side door.
    - `sources` is the union of the inputs, assembled in code. A model asked to copy
      source lists will eventually drop one, and provenance is not best-effort.
    - `id` is derived from content after the merge.

    The model decides what is one ask and how to say it. Everything mechanical stays
    mechanical.
    """

    model_config = ConfigDict(extra="forbid")

    merge: bool = Field(description="True if every signal in this cluster is the same ask.")
    statement: str = Field(description="The merged ask, stated once, in the requester's terms.")
    title: str = Field(max_length=120)
    detail: str = ""
    owner: str = Field(description="One of: me, delegate, waiting.")
    suggested_action: str = Field(
        description="One of: reply, schedule, delegate, create_issue, no_action."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_judgment: bool = False
    urgency_reason: str = Field(
        description="One sentence explaining the urgency score you were given. "
        "You are not setting the score; you are explaining it."
    )
    split_groups: list[list[int]] | None = Field(
        default=None,
        description="When merge is false, partition the input signal indices into the "
        "distinct asks they actually represent.",
    )


EXPORTS: dict[str, type[BaseModel]] = {
    "signal-extraction.schema.json": SignalExtraction,
    "cluster-merge.schema.json": ClusterMerge,
    "proposed-action.schema.json": ProposedAction,
    "ledger.schema.json": Ledger,
}


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema(mode="serialization")


def render(model: type[BaseModel]) -> str:
    return json.dumps(schema_for(model), indent=2, sort_keys=True) + "\n"


def main(check: bool = False) -> int:
    CONTRACTS.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for filename, model in EXPORTS.items():
        path = CONTRACTS / filename
        expected = render(model)
        if check:
            if not path.exists() or path.read_text() != expected:
                stale.append(filename)
        else:
            path.write_text(expected)
            print(f"wrote {path.relative_to(CONTRACTS.parents[2])}")
    if stale:
        print("stale schemas: " + ", ".join(stale), file=sys.stderr)
        print("run: uv run python scripts/export_schemas.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(check="--check" in sys.argv))
