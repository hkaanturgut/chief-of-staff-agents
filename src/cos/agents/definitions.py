"""Loading agent definitions from `agents/*.yaml`.

The repository is the source of truth (Constitution VII). Nothing reads instructions from
anywhere else, and `scripts/provision_agents.py` pushes these to Foundry rather than the
other way round.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cos.agents.runner import ModelTier
from cos.settings import REPO_ROOT, ModelSettings

AGENTS_DIR = REPO_ROOT / "agents"


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str = ""
    tier: str = Field(description="ingest or strong")
    called_from: str = Field(description="orchestrator or code")
    instructions: str = Field(min_length=1)
    connected_agents: list[str] = Field(default_factory=list)

    def model_tier(self, models: ModelSettings) -> ModelTier:
        source = models.ingest if self.tier == "ingest" else models.strong
        return ModelTier(deployment=source.deployment, version=source.version)


@lru_cache(maxsize=1)
def load_all(directory: Path | None = None) -> dict[str, AgentDefinition]:
    target = directory or AGENTS_DIR
    definitions: dict[str, AgentDefinition] = {}
    for path in sorted(target.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text()) or {}
        definition = AgentDefinition.model_validate(payload)
        if definition.name in definitions:
            raise ValueError(f"duplicate agent name {definition.name!r} in {path}")
        definitions[definition.name] = definition
    if not definitions:
        raise FileNotFoundError(f"no agent definitions found in {target}")
    return definitions


def get(name: str) -> AgentDefinition:
    definitions = load_all()
    if name not in definitions:
        raise KeyError(f"unknown agent {name!r}; have {sorted(definitions)}")
    return definitions[name]
