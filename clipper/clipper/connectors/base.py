"""What every posting connector shares.

A connector knows one platform: how the user signs in with the developer app
they registered there, how a clip is uploaded through the platform's own API,
and how to ask what became of a post. When to post is not decided here; that is
outbox.py.

Each connector keeps the addresses it talks to as class attributes, so a test
can point it at a local stand-in.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from .. import settings
from ..config import Config

TIMEOUT = (15, 120)  # seconds to connect, seconds to wait for an answer
UPLOAD_TIMEOUT = (15, 900)


class ConnectorError(RuntimeError):
    """A platform refused a request or could not be reached.

    retry      worth sending again later without anyone doing anything
    wait       how long to leave it before that, in seconds (0: the usual back-off)
    reconnect  the sign-in is no longer good: the user has to connect the account again
    """

    def __init__(self, message: str, retry: bool = False, reconnect: bool = False, wait: int = 0):
        super().__init__(message)
        self.retry, self.reconnect, self.wait = retry, reconnect, wait


@dataclass
class Sent:
    """What a platform said when it took a clip."""

    id: str  # the platform's own id for the upload or the post
    state: str  # scheduled (the platform publishes it at the time given) | processing | posted | draft
    link: str = ""
    note: str = ""  # something the user should know, e.g. that it was posted as private


def pkce() -> tuple[str, str]:
    """A fresh PKCE verifier and its S256 challenge (RFC 7636: base64url of the SHA-256, no padding)."""
    verifier = _secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


class Connector:
    name = ""  # "youtube"
    label = ""  # "YouTube"
    native_schedule = False  # the platform itself publishes at a future time, so the clip can be handed over at once
    redirect_host = "127.0.0.1"  # the loopback name this platform accepts in a redirect address
    redirect_path = ""  # and the path after the port
    account_key = ""  # where the account's details are kept in the settings; the platform's name unless two share a sign-in
    needs_secret = True  # the developer app has a secret as well as an id

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.data_root

    # --- what is kept on this computer ------------------------------------------

    @property
    def key(self) -> str:
        return self.account_key or self.name

    def secret(self, name: str) -> str:
        return settings.get_secret(self.root, f"{self.key}_{name}")

    def set_secret(self, name: str, value: str) -> None:
        settings.set_secret(self.root, f"{self.key}_{name}", value)

    def remembered(self) -> dict[str, Any]:
        return settings.account(self.root, self.key)

    def remember(self, **values: Any) -> None:
        settings.set_account(self.root, self.key, values)

    def app(self) -> dict[str, Any]:
        """The developer app's details as far as the page may see them: its id, and whether it is complete."""
        client_id, has_secret = str(self.remembered().get("client_id", "")), bool(self.secret("client_secret"))
        return {"client_id": client_id, "has_secret": has_secret, "ready": bool(client_id) and (has_secret or not self.needs_secret)}

    def save_app(self, client_id: str, client_secret: str = "") -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id:
            raise ConnectorError(f"Paste the {self.label} app's client ID first.")
        if client_secret:
            self.set_secret("client_secret", client_secret)
        elif self.needs_secret and not self.secret("client_secret"):
            raise ConnectorError(f"Paste the {self.label} app's client secret as well.")
        self.remember(client_id=client_id)

    def redirect_uri(self, port: int) -> str:
        return f"http://{self.redirect_host}:{port}{self.redirect_path}"

    def account(self) -> dict[str, Any]:
        """The connected account as the app shows it ({} when not connected): at least a name."""
        kept = self.remembered()
        return {k: v for k, v in kept.items() if k != "client_id"} if kept.get("connected") else {}

    def connected(self) -> bool:
        return bool(self.account())

    def forget(self) -> None:
        """Drop the sign-in but keep the developer app's details, so connecting again is one click."""
        kept = self.remembered()
        settings.set_account(self.root, self.key, None)
        if kept.get("client_id"):
            self.remember(client_id=kept["client_id"])

    # --- talking to the platform -------------------------------------------------

    def call(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """One request. Not being able to reach the platform is a problem worth trying again later."""
        kwargs.setdefault("timeout", TIMEOUT)
        try:
            return requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise ConnectorError(f"{self.label} could not be reached ({type(exc).__name__})", retry=True) from exc

    @staticmethod
    def body(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    # --- what each platform does its own way -------------------------------------

    def auth_url(self, redirect_uri: str, state: str) -> tuple[str, dict[str, Any]]:
        """The address to send the user's browser to, and what to keep until they come back."""
        raise NotImplementedError

    def finish(self, redirect_uri: str, query: dict[str, str], kept: dict[str, Any]) -> dict[str, Any]:
        """Complete the sign-in with what the platform sent back; returns the account."""
        raise NotImplementedError

    def disconnect(self) -> None:
        """Withdraw the access where the platform allows it, and forget the sign-in."""
        self.forget()

    def send(self, clip: dict, video: Path, when: datetime | None) -> Sent:
        """Upload a finished clip. `when` is a future publishing time, or None for now."""
        raise NotImplementedError

    def status(self, post: dict) -> dict[str, Any]:
        """What became of a post: any of state, link, views, error."""
        return {}

    def unfit(self, clip: dict, seconds: float, size: int) -> str:
        """Why this clip cannot go to this platform at all (too long, too big), or empty."""
        return ""
