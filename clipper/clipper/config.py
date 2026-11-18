"""Configuration: defaults here, overrides from config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

ROOT = Path(__file__).resolve().parent.parent  # the clipper/ project directory


@dataclass
class Brand:
    name: str = "Clips"
    game: str = "GTA 6"
    handle: str = ""  # shown as a small watermark, e.g. "@yourchannel"


@dataclass
class Paths:
    inbox: str = "inbox"
    work: str = "work"
    out: str = "out"
    state: str = "state"
    reports: str = "reports"
    models: str = ""  # default: $CLIPPER_MODELS or ~/.cache/clipper/models


@dataclass
class Source:
    type: str = "folder"  # folder | urls | ytdlp
    path: str = ""  # folder: directory of video files
    file: str = ""  # urls: text file with one direct video link per line
    url: str = ""  # ytdlp: channel, playlist or video page
    creator: str = ""  # who is in the footage; used in titles and credits
    permission: str = ""  # why this footage may be used (campaign name, own footage, ...)
    max_items: int = 2  # per run
    max_hours: float = 3.0  # only the first N hours of a long video are fetched
    chat_file: str = ""  # optional chat log (JSON list of {"t": seconds}) for a folder source


@dataclass
class Transcribe:
    threads: int = 4
    max_segment: float = 18.0  # seconds; longer speech runs are split
    vad_threshold: float = 0.4


@dataclass
class Moments:
    max_per_source: int = 8  # most clips taken from one video
    min_len: float = 18.0
    max_len: float = 45.0
    lead_in: float = 1.5  # seconds of context kept before the excitement starts
    min_gap: float = 20.0  # seconds between two clips from the same source
    min_score: float = 1.0
    w_loud: float = 1.0
    w_rate: float = 0.5
    w_words: float = 1.0
    w_chat: float = 1.0
    hype_words: list[str] = field(
        default_factory=lambda: [
            "no way", "oh my god", "omg", "wait wait", "let's go", "lets go", "holy", "insane",
            "crazy", "craziest", "impossible", "clip that", "go go", "jump", "i'm dead", "explode",
            "exploded", "can't believe", "cannot believe", "wow", "whoa", "unreal", "what the",
            "are you kidding", "oh no", "hold on",
        ]
    )


@dataclass
class Captions:
    font: str = "Montserrat ExtraBold"
    size: int = 108
    mode: str = "highlight"  # highlight (word pops in colour) | sweep (colour fills across the line)
    uppercase: bool = True
    max_words: int = 3
    max_chars: int = 16
    color: str = "#FFFFFF"
    active_color: str = "#FFE600"
    outline_color: str = "#000000"
    outline: float = 7.0
    shadow: float = 3.0
    active_scale: int = 108  # percent; 100 turns the pop off
    y: float = 0.74  # vertical position as a share of the frame height
    mask_profanity: bool = True


@dataclass
class Render:
    layout: str = "blur"  # blur | crop | stack
    width: int = 1080
    height: int = 1920
    fps: int = 30
    zoom: float = 1.5  # blur layout: how far the picture is zoomed in (1.0 shows the full width)
    picture_y: float = 0.42  # blur layout: vertical centre of the picture
    crop_x: float = 0.5  # crop layout: horizontal centre of the crop (0 left, 1 right)
    facecam: list[float] = field(default_factory=list)  # stack layout: [x, y, w, h] as shares of the frame
    facecam_share: float = 0.36  # stack layout: share of the height given to the facecam
    crf: int = 20
    preset: str = "veryfast"
    loudness: float = -14.0
    hook_seconds: float = 3.5


@dataclass
class Campaign:
    name: str = ""
    paid: bool = False
    disclosure_label: str = "Paid partnership"  # burned into the video when paid
    disclosure_tag: str = "#ad"  # first word of every caption when paid
    required_hashtags: list[str] = field(default_factory=list)
    credit: str = ""  # e.g. "Clip from @streamer"


@dataclass
class Seo:
    hashtags: list[str] = field(
        default_factory=lambda: ["#gta6", "#gtavi", "#gta", "#gaming", "#gamingclips", "#rockstargames", "#gta6clips", "#vicecity"]
    )
    youtube_tags: list[str] = field(
        default_factory=lambda: ["gta 6", "gta vi", "grand theft auto 6", "gta 6 clips", "gta 6 funny moments", "gta 6 gameplay"]
    )
    youtube_category: str = "20"  # Gaming
    fan_channel_note: str = "Unofficial fan clips. Not affiliated with Rockstar Games or Take-Two."


@dataclass
class Schedule:
    timezone: str = "America/New_York"
    slots: list[str] = field(default_factory=lambda: ["11:30", "15:00", "19:00", "21:30"])
    min_lead_minutes: int = 20
    max_days_ahead: int = 3


@dataclass
class Publish:
    mode: str = "dry_run"  # dry_run | live
    profile: str = ""  # Upload-Post profile name that the social accounts are connected to
    platforms: list[str] = field(default_factory=lambda: ["tiktok", "instagram", "youtube", "facebook"])
    facebook_page_id: str = ""
    tiktok_post_mode: str = "DIRECT_POST"  # DIRECT_POST | MEDIA_UPLOAD (lands as a draft in the TikTok app)
    tiktok_privacy_level: str = ""  # empty keeps the account default
    youtube_privacy: str = "public"
    hold_for_review: bool = False  # plan and render, but schedule nothing until `approve`


@dataclass
class Gates:
    min_words: int = 12
    min_speech_share: float = 0.25
    max_overlap: float = 0.3
    max_text_similarity: float = 0.8
    banned_terms: list[str] = field(
        default_factory=lambda: ["stake", "casino", "roobet", "gambling", "slots", "rainbet", "polymarket", "election"]
    )


@dataclass
class Config:
    brand: Brand = field(default_factory=Brand)
    paths: Paths = field(default_factory=Paths)
    sources: list[Source] = field(default_factory=lambda: [Source(type="folder", path="inbox")])
    transcribe: Transcribe = field(default_factory=Transcribe)
    moments: Moments = field(default_factory=Moments)
    captions: Captions = field(default_factory=Captions)
    render: Render = field(default_factory=Render)
    campaign: Campaign = field(default_factory=Campaign)
    seo: Seo = field(default_factory=Seo)
    schedule: Schedule = field(default_factory=Schedule)
    publish: Publish = field(default_factory=Publish)
    gates: Gates = field(default_factory=Gates)
    root: Path = ROOT

    def path(self, name: str) -> Path:
        """Absolute path of one of the working directories."""
        p = Path(getattr(self.paths, name))
        return p if p.is_absolute() else self.root / p

    @property
    def models_dir(self) -> Path:
        if self.paths.models:
            return Path(self.paths.models).expanduser()
        return Path(os.environ.get("CLIPPER_MODELS", "~/.cache/clipper/models")).expanduser()

    @property
    def fonts_dir(self) -> Path:
        return ROOT / "assets" / "fonts"


class ConfigError(ValueError):
    pass


def _fill(cls: type, data: dict[str, Any], where: str) -> Any:
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown setting(s) in [{where}]: {', '.join(sorted(unknown))}")
    kwargs = {}
    for name, value in data.items():
        kind = hints[name]
        if is_dataclass(kind):
            kwargs[name] = _fill(kind, value, name)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load(path: str | Path | None = None) -> Config:
    """Read config.toml (or the given file); a missing file means all defaults."""
    path = Path(path) if path else ROOT / "config.toml"
    data: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    sources = data.pop("sources", None)
    cfg = _fill(Config, data, "config")
    if sources is not None:
        cfg.sources = [_fill(Source, s, "sources") for s in sources]
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.publish.mode not in ("dry_run", "live"):
        raise ConfigError("publish.mode must be dry_run or live")
    if cfg.render.layout not in ("blur", "crop", "stack"):
        raise ConfigError("render.layout must be blur, crop or stack")
    if cfg.render.layout == "stack" and len(cfg.render.facecam) != 4:
        raise ConfigError("render.layout = stack needs render.facecam = [x, y, w, h]")
    if cfg.captions.mode not in ("highlight", "sweep"):
        raise ConfigError("captions.mode must be highlight or sweep")
    if not 5 <= cfg.moments.min_len <= cfg.moments.max_len:
        raise ConfigError("moments: need 5 <= min_len <= max_len")
    for slot in cfg.schedule.slots:
        hh, _, mm = slot.partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ConfigError(f"schedule.slots: {slot!r} is not HH:MM")
    for s in cfg.sources:
        if s.type not in ("folder", "urls", "ytdlp"):
            raise ConfigError(f"sources: unknown type {s.type!r}")
        if s.type != "folder" and not s.permission:
            raise ConfigError(f"sources: a {s.type} source needs `permission` (why this footage may be used)")
