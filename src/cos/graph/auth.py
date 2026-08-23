"""Microsoft Graph authentication.

Deliberately separate from the Azure credential chain. The Foundry account and the
mailbox live in different directories, and one ambient `DefaultAzureCredential` cannot
serve both — see `docs/decisions.md` D-003.

Delegated only, via MSAL device code with a token cache on disk. The user signs in once
and the refresh token carries the session from there. Run `cos login` within thirty
minutes of any demonstration: an expired token is the single most likely way a live run
fails, and re-authenticating in front of an audience is a twenty-second recovery only if
it has been rehearsed.

The cache file is chmod 600 and gitignored. It holds a refresh token, which is a
credential — it must never reach the repository.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass

import msal

from cos.logging import get_logger
from cos.settings import REPO_ROOT

log = get_logger("graph.auth")

CACHE_PATH = REPO_ROOT / ".msal_cache.json"

# The authority for an app registered with signInAudience=PersonalMicrosoftAccount.
# `/common` would be wrong here: it advertises work and school accounts too, and the
# token endpoint rejects them for a consumer-only app with a confusing error.
CONSUMERS_AUTHORITY = "https://login.microsoftonline.com/consumers"

# Requested at sign-in. openid, profile, and offline_access are implicit in MSAL and are
# rejected if passed explicitly.
GRAPH_SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
]

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphAuthError(RuntimeError):
    """Raised when no usable token can be obtained."""


@dataclass(frozen=True)
class GraphAuth:
    """Holds a signed-in MSAL app and hands out access tokens."""

    client_id: str
    authority: str = CONSUMERS_AUTHORITY

    def _cache(self) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        if CACHE_PATH.exists():
            cache.deserialize(CACHE_PATH.read_text())
        return cache

    def _persist(self, cache: msal.SerializableTokenCache) -> None:
        if not cache.has_state_changed:
            return
        CACHE_PATH.write_text(cache.serialize())
        # The cache holds a refresh token. Readable by this user only.
        os.chmod(CACHE_PATH, stat.S_IRUSR | stat.S_IWUSR)

    def _app(self, cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
        return msal.PublicClientApplication(
            self.client_id, authority=self.authority, token_cache=cache
        )

    def acquire_silent(self) -> str | None:
        """A token from the cache, or None. Never prompts."""
        cache = self._cache()
        app = self._app(cache)
        accounts = app.get_accounts()
        if not accounts:
            return None
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        self._persist(cache)
        if result and "access_token" in result:
            return str(result["access_token"])
        return None

    def login(self, *, prompt: Callable[[str], None] | None = None) -> str:
        """Device code sign-in. Prints the code and blocks until the user completes it."""
        cache = self._cache()
        app = self._app(cache)

        flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
        if "user_code" not in flow:
            raise GraphAuthError(
                "could not start the device code flow: "
                f"{flow.get('error_description', json.dumps(flow))}"
            )

        message = str(flow["message"])
        (prompt or print)(message)

        result = app.acquire_token_by_device_flow(flow)
        self._persist(cache)

        if "access_token" not in result:
            error = str(result.get("error", ""))
            description = str(result.get("error_description", json.dumps(result)))
            # The consumer endpoint gives roughly seven minutes. Expiry is by far the
            # most common outcome and it is not an error worth a stack trace — it means
            # nobody was at the keyboard.
            if "AADSTS70016" in description or error == "expired_token":
                raise GraphAuthError(
                    "the device code expired before it was entered. "
                    "Run `cos login` again while you are at the keyboard — you have "
                    "about seven minutes from when the code is printed."
                )
            raise GraphAuthError(f"sign-in failed: {description}")

        log.info("signed in to Microsoft Graph", scopes=len(GRAPH_SCOPES))
        return str(result["access_token"])

    def token(self) -> str:
        """A token, from cache if possible. Raises rather than prompting mid-run.

        A pipeline run must never block on an interactive prompt: in CI nobody is
        watching, and on stage the prompt appears in the middle of the demo. Sign in
        deliberately, beforehand.
        """
        token = self.acquire_silent()
        if token is None:
            raise GraphAuthError(
                "no cached Microsoft Graph token. Run `cos login` first — and run it "
                "within 30 minutes of any live demonstration."
            )
        return token

    def signed_in_account(self) -> str | None:
        app = self._app(self._cache())
        accounts = app.get_accounts()
        return str(accounts[0].get("username")) if accounts else None


def from_settings() -> GraphAuth:
    from cos.settings import load_environment

    env = load_environment()
    env.require("graph_client_id")
    assert env.graph_client_id is not None
    return GraphAuth(client_id=env.graph_client_id)
