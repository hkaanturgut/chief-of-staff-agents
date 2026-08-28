"""Typed configuration.

Config is loaded once into frozen models and fails loudly on a missing required key. A
run that starts with a half-populated config and discovers the gap three model calls
later has already cost money and time.

Nothing here reads a secret. Every value is an identifier or a policy knob; credentials
come from the Azure CLI, from a device-code token cache, or from federated identity.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- config/settings.yaml --------------------------------------------------------------


class RunSettings(Frozen):
    dry_run: bool = True
    max_actions_per_run: int = Field(default=5, ge=0)


class WindowSettings(Frozen):
    lookback_hours: int = Field(default=24, gt=0)
    extend_over_non_working_days: bool = True
    calendar_lookahead_business_days: int = Field(default=2, ge=0)
    timezone: str = "America/Toronto"


class SourceSettings(Frozen):
    scheduled: list[str] = Field(default_factory=lambda: ["mail", "calendar"])
    attended: list[str] = Field(default_factory=lambda: ["mail", "chat", "calendar"])
    chat_context_messages: int = Field(default=8, ge=0)


class ModelTier(Frozen):
    deployment: str
    version: str


class ModelSettings(Frozen):
    ingest: ModelTier
    strong: ModelTier


class UrgencyWeights(Frozen):
    due_proximity: int = 50
    sender_importance: int = 30
    distinct_sources: int = 20


class UrgencySettings(Frozen):
    weights: UrgencyWeights = Field(default_factory=UrgencyWeights)
    due_imminent_hours: int = Field(default=24, gt=0)
    due_horizon_hours: int = Field(default=168, gt=0)
    sources_saturation: int = Field(default=3, ge=1)


class StalenessSettings(Frozen):
    flag_after_runs: int = Field(default=3, ge=1)


class GitHubSettings(Frozen):
    repo: str
    branch_prefix: str = "cos/run-"
    high_risk_label: str = "high-risk"


class Settings(Frozen):
    run: RunSettings
    window: WindowSettings
    sources: SourceSettings
    models: ModelSettings
    urgency: UrgencySettings
    staleness: StalenessSettings
    github: GitHubSettings


# --- config/allowed_recipients.yaml ----------------------------------------------------


class AllowedRecipients(Frozen):
    """The kill switch. Empty means nothing can be sent to anyone, which is correct."""

    addresses: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    chat_ids: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)

    def permits_address(self, address: str) -> bool:
        value = address.strip().lower()
        if not value:
            return False
        if value in {a.strip().lower() for a in self.addresses}:
            return True
        if "@" not in value:
            return False
        domain = value.rsplit("@", 1)[1]
        # A domain entry is a much larger blast radius than an address entry. It is
        # supported because a demo tenant is a legitimate use, and audited because of it.
        return domain in {d.strip().lower().lstrip("@") for d in self.domains}

    def permits_chat(self, chat_id: str) -> bool:
        return chat_id in set(self.chat_ids)

    def permits_repo(self, repo: str) -> bool:
        return repo.strip().lower() in {r.strip().lower() for r in self.repos}


# --- config/important_senders.yaml -----------------------------------------------------


class ImportantSender(Frozen):
    match: str
    weight: float = Field(ge=0.0, le=1.0)
    note: str | None = None


class ImportantSenders(Frozen):
    senders: list[ImportantSender] = Field(default_factory=list)

    def weight_for(self, address: str) -> float:
        """Explicit only. An absent sender contributes zero — see docs/decisions.md D-009."""
        value = address.strip().lower()
        best = 0.0
        for entry in self.senders:
            m = entry.match.strip().lower()
            hit = value == m or (m.startswith("@") and value.endswith(m))
            if hit:
                best = max(best, entry.weight)
        return best


# --- environment -----------------------------------------------------------------------

Env = Literal[
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_RESOURCE_GROUP",
    "AZURE_LOCATION",
    "FOUNDRY_ACCOUNT_NAME",
    "FOUNDRY_PROJECT_NAME",
    "FOUNDRY_PROJECT_ENDPOINT",
    "MODEL_INGEST",
    "MODEL_STRONG",
    "GRAPH_TENANT_ID",
    "GRAPH_CLIENT_ID",
    "GRAPH_MAILBOX",
    "GITHUB_REPO",
]


class Environment(Frozen):
    """Resource identifiers, not credentials.

    Azure and Graph tenants are separate fields on purpose. The Foundry account and the
    mailbox live in different directories, so one ambient credential cannot serve both.
    See docs/decisions.md D-003.
    """

    azure_subscription_id: str | None = None
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_resource_group: str | None = None
    foundry_project_endpoint: str | None = None
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_mailbox: str | None = None
    github_repo: str | None = None

    def require(self, *names: str) -> None:
        """Fail before doing anything expensive, naming every gap at once."""
        missing = [n for n in names if not getattr(self, n, None)]
        if missing:
            keys = ", ".join(n.upper() for n in missing)
            raise RuntimeError(
                f"missing required configuration: {keys}. "
                "Copy .env.example to .env and fill it in; see the quickstart."
            )


def _read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"missing config file: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


@lru_cache(maxsize=1)
def load_settings(config_dir: Path | None = None) -> Settings:
    return Settings.model_validate(_read_yaml((config_dir or CONFIG_DIR) / "settings.yaml"))


@lru_cache(maxsize=1)
def load_allowed_recipients(config_dir: Path | None = None) -> AllowedRecipients:
    path = (config_dir or CONFIG_DIR) / "allowed_recipients.yaml"
    return AllowedRecipients.model_validate(_read_yaml(path))


@lru_cache(maxsize=1)
def load_important_senders(config_dir: Path | None = None) -> ImportantSenders:
    path = (config_dir or CONFIG_DIR) / "important_senders.yaml"
    return ImportantSenders.model_validate(_read_yaml(path))


@lru_cache(maxsize=1)
def load_environment() -> Environment:
    load_dotenv(REPO_ROOT / ".env", override=False)
    get = os.environ.get
    return Environment(
        azure_subscription_id=get("AZURE_SUBSCRIPTION_ID"),
        azure_tenant_id=get("AZURE_TENANT_ID"),
        azure_client_id=get("AZURE_CLIENT_ID"),
        azure_resource_group=get("AZURE_RESOURCE_GROUP"),
        foundry_project_endpoint=get("FOUNDRY_PROJECT_ENDPOINT"),
        graph_tenant_id=get("GRAPH_TENANT_ID"),
        graph_client_id=get("GRAPH_CLIENT_ID"),
        graph_mailbox=get("GRAPH_MAILBOX"),
        github_repo=get("GITHUB_REPO"),
    )
