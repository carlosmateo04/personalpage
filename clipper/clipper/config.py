"""Configuration: defaults here, overrides from config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

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
    url: str = ""  # ytdlp: a channel's clips or videos page, a playlist, a video, or a search ("ytsearch10:gta 6 glitches")
    creator: str = ""  # who is in the footage; left empty, the channel name is used
    permission: str = ""  # on what basis this footage is used (campaign, the creator's clip policy, own footage); empty = none on file
    section: str = ""  # section these clips go to unless their content says otherwise
    max_items: int = 2  # videos taken per run
    look_at: int = 15  # how many listed videos are considered
    min_views: int = 0  # skip videos with fewer views
    min_minutes: float = 0.0
    max_minutes: float = 0.0  # 0 = no limit
    category: str = ""  # only videos whose game/category contains this text
    title_any: list[str] = field(default_factory=list)  # the title must contain one of these
    title_none: list[str] = field(default_factory=list)  # the title must contain none of these
    max_hours: float = 3.0  # only the first N hours of a long video are fetched
    chat_file: str = ""  # optional chat log (JSON list of {"t": seconds}) for a folder source


@dataclass
class Section:
    name: str = "moments"
    label: str = "HIGHLIGHT"  # badge shown on the clip
    color: str = "#FF2E93"
    cues: list[str] = field(default_factory=list)  # words and phrases that mark this kind of clip
    hashtags: list[str] = field(default_factory=list)
    youtube_playlist_id: str = ""


def default_sections() -> list[Section]:
    return [
        Section("findings", "NEW FINDING", "#FFE600",
                ["easter egg", "secret", "hidden", "i found", "we found", "found a", "discovered", "did you know", "detail",
                 "nobody noticed", "reference", "never seen", "mystery", "bigfoot", "ufo", "alien", "ghost"],
                ["#gta6secrets", "#gta6eastereggs"]),
        Section("tricks", "TRICK", "#00E0FF",
                ["how to", "trick", "tip", "pro tip", "fastest way", "easy way", "easiest way", "unlock", "cheat", "shortcut",
                 "all you have to do", "you just need", "money fast", "best way"],
                ["#gta6tips", "#gta6tricks"]),
        Section("glitches", "GLITCH", "#7CFF4F",
                ["glitch", "glitched", "bug", "bugged", "broken", "clipped through", "fell through", "under the map",
                 "out of bounds", "launched", "physics"],
                ["#gta6glitch", "#gta6bugs"]),
        Section("moments", "HIGHLIGHT", "#FF2E93", [], ["#gta6highlights", "#gta6moments"]),
    ]


@dataclass
class Filters:
    """Keep only gameplay-only footage: nobody on camera, nobody talking over the game."""

    no_facecam: bool = True
    no_commentary: bool = True
    facecam_frames: int = 16  # frames looked at per clip
    facecam_share: float = 0.6  # a face in the same spot in this share of the frames is a facecam
    require_speech: bool = False  # gameplay-only clips often have no speech at all
    commentary_words: list[str] = field(
        default_factory=lambda: [
            "chat", "clip that", "clip it", "gifted", "subs", "donation", "donations", "this game", "gta", "gta 6", "gta six",
            "grand theft auto", "rockstar", "gameplay", "walkthrough", "cutscene", "cutscenes", "npc", "npcs", "easter egg",
            "playstation", "ps5", "xbox", "frame rate", "fps", "this video", "today's video", "my channel",
            "welcome back to", "what's up guys", "hey guys", "let me know in the comments", "like and subscribe",
        ]
    )


@dataclass
class Rights:
    block_channels: list[str] = field(default_factory=lambda: ["Rockstar Games", "Take-Two Interactive"])  # never taken from
    credit_format: str = "Credit: {creator}"  # shown on the clip and in every caption


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
    min_score: float = 1.5
    whole_clip_max: float = 62.0  # videos up to this length (for example Twitch clips) are used whole
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
    # Pay terms from the campaign brief; they drive the earnings estimate in the report.
    rate_per_1k: float = 0.0  # paid per 1,000 views
    min_payout: float = 0.0  # a post that earns less than this is paid nothing
    max_payout: float = 0.0  # cap per post (0 = no cap)
    fee: float = 0.0  # marketplace fee taken from payouts (0.10 = 10%)
    counted_platforms: list[str] = field(default_factory=list)  # platforms whose views are paid (empty = all)

    def payout(self, platform: str, views: int) -> float:
        """Estimated net payout for one post, before the campaign's own review."""
        if self.rate_per_1k <= 0 or (self.counted_platforms and platform not in self.counted_platforms):
            return 0.0
        gross = views / 1000 * self.rate_per_1k
        if gross < self.min_payout:
            return 0.0
        if self.max_payout > 0:
            gross = min(gross, self.max_payout)
        return round(gross * (1 - self.fee), 2)


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
    min_words_whole: int = 4  # for short clips that are used whole
    min_speech_share: float = 0.25
    max_overlap: float = 0.3
    max_text_similarity: float = 0.6
    banned_terms: list[str] = field(
        default_factory=lambda: ["stake", "casino", "roobet", "gambling", "slots", "rainbet", "polymarket", "election"]
    )


@dataclass
class Config:
    brand: Brand = field(default_factory=Brand)
    paths: Paths = field(default_factory=Paths)
    sources: list[Source] = field(default_factory=lambda: [Source(type="folder", path="inbox")])
    sections: list[Section] = field(default_factory=default_sections)
    rights: Rights = field(default_factory=Rights)
    filters: Filters = field(default_factory=Filters)
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

    @property
    def data_root(self) -> Path:
        """Where the working directories live: $CLIPPER_DATA when set, otherwise the project folder.

        A separate data folder lets the code be replaced by a fresh download
        without losing the ledger, the clips or the inbox.
        """
        return Path(os.environ["CLIPPER_DATA"]).expanduser() if os.environ.get("CLIPPER_DATA") else self.root

    def path(self, name: str) -> Path:
        """Absolute path of one of the working directories."""
        p = Path(getattr(self.paths, name))
        return p if p.is_absolute() else self.data_root / p

    def section(self, name: str) -> Section:
        """The named section, or the catch-all (the one called "moments", else the last)."""
        for sec in self.sections:
            if sec.name == name:
                return sec
        return next((sec for sec in self.sections if sec.name == "moments"), self.sections[-1])

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
        inner = get_args(kind)
        if is_dataclass(kind):
            kwargs[name] = _fill(kind, value, name)
        elif get_origin(kind) is list and inner and is_dataclass(inner[0]):
            kwargs[name] = [_fill(inner[0], v, name) for v in value]
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
    cfg = _fill(Config, data, "config")
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
    if not 0 <= cfg.campaign.fee < 1:
        raise ConfigError("campaign.fee must be a share between 0 and 1 (0.10 = 10%)")
    if cfg.publish.mode == "live" and cfg.campaign.paid and not cfg.campaign.name:
        raise ConfigError("campaign.paid is true but campaign.name is empty: name the campaign you joined before going live")
    for s in cfg.sources:
        if s.type not in ("folder", "urls", "ytdlp"):
            raise ConfigError(f"sources: unknown type {s.type!r}")
        if s.type == "ytdlp" and not s.url:
            raise ConfigError("sources: a ytdlp source needs `url`")
    if not cfg.sections:
        raise ConfigError("at least one [[sections]] entry is needed")
    names = [sec.name for sec in cfg.sections]
    if len(set(names)) != len(names):
        raise ConfigError("sections: names must be unique")
    for src in cfg.sources:
        if src.section and src.section not in names:
            raise ConfigError(f"sources: unknown section {src.section!r}")
