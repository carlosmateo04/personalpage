"""Direct connections to the platforms: the clipper's own posting, without a service in between."""

from __future__ import annotations

from ..config import Config
from .base import Connector, ConnectorError, Sent
from .meta import Facebook, Instagram
from .tiktok import TikTok
from .youtube import YouTube

ALL: dict[str, type[Connector]] = {"youtube": YouTube, "tiktok": TikTok, "instagram": Instagram, "facebook": Facebook}

__all__ = ["ALL", "Connector", "ConnectorError", "Sent", "get", "names"]


def get(name: str, cfg: Config) -> Connector:
    try:
        return ALL[name](cfg)
    except KeyError:
        raise ConnectorError(f"there is no direct connection for {name}") from None


def names() -> list[str]:
    return list(ALL)
