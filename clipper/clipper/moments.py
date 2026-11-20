"""Find the moments worth clipping.

Every half second gets a score from four signals: how much louder the audio is
than the surrounding minutes, how fast people are talking, whether hype words are
being said, and (when a chat log exists) how fast chat is moving. Peaks of that
score become clips whose edges are snapped to sentence boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Moments
from .transcribe import Word

HOP = 0.5
_END = re.compile(r"[.!?]$")


@dataclass
class Sentence:
    s: float
    e: float
    first: int  # index of the first word
    last: int  # index of the last word


@dataclass
class Candidate:
    start: float
    end: float
    peak: float
    score: float
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.w for w in self.words)

    @property
    def duration(self) -> float:
        return self.end - self.start


def sentences(words: list[Word], max_gap: float = 0.8) -> list[Sentence]:
    out: list[Sentence] = []
    first = 0
    for i, w in enumerate(words):
        last_word = i == len(words) - 1
        gap = (words[i + 1].s - w.e) if not last_word else 0.0
        if last_word or _END.search(w.w) or gap > max_gap:
            out.append(Sentence(words[first].s, w.e, first, i))
            first = i + 1
    return out


def _robust_z(x: np.ndarray, window: int) -> np.ndarray:
    """How far each value sits above the median of its surroundings, in robust deviations."""
    n = len(x)
    z = np.zeros(n)
    half = window // 2
    for i in range(n):
        seg = x[max(0, i - half) : min(n, i + half + 1)]
        med = np.median(seg)
        mad = np.median(np.abs(seg - med)) * 1.4826
        z[i] = (x[i] - med) / max(mad, 1.0)
    return z


def _smooth(x: np.ndarray, sigma: float) -> np.ndarray:
    radius = int(sigma * 3)
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    return np.convolve(x, k / k.sum(), mode="same")


def loudness_db(wav: Path) -> np.ndarray:
    audio, rate = sf.read(str(wav), dtype="float32", always_2d=True)
    audio = audio[:, 0]
    hop = int(rate * HOP)
    n = len(audio) // hop
    frames = audio[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames**2).mean(axis=1) + 1e-10)
    return 20 * np.log10(rms)


def _density(hits: np.ndarray, sigma: float) -> np.ndarray:
    """Hits within a few seconds of each moment: one hit counts 1 at its own time and fades with distance."""
    radius = int(sigma * 3)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    return np.convolve(hits, kernel, mode="same")


def score_track(cfg: Moments, wav: Path, words: list[Word], chat_times: list[float] | None = None, cues: list[str] | None = None) -> np.ndarray:
    db = loudness_db(wav)
    n = len(db)
    loud = np.clip(_robust_z(db, int(180 / HOP)), 0, 4)

    rate = np.zeros(n)
    hype = np.zeros(n)
    patterns = [re.compile(r"\b" + re.escape(h.lower()) + r"\b") for h in [*cfg.hype_words, *(cues or [])]]
    for i, w in enumerate(words):
        b = min(n - 1, int(w.s / HOP))
        rate[b] += 1
        if w.w.endswith("!") or w.w.endswith("?"):
            hype[b] += 1.0
        context = " ".join(x.w for x in words[i : i + 5]).lower()
        context = re.sub(r"[^a-z' ]", "", context)
        for p in patterns:
            if p.match(context):
                hype[b] += 1.0
                break
    rate = _smooth(rate, 4 / HOP) / HOP  # words per second
    spoken = rate[rate > 0.2]
    if len(spoken) > 10:
        rate_z = np.clip((rate - np.median(spoken)) / max(spoken.std(), 0.3), 0, 3)
    else:
        rate_z = np.zeros(n)

    score = cfg.w_loud * _smooth(loud, 2 / HOP) + cfg.w_rate * rate_z + cfg.w_words * np.clip(_density(hype, 4 / HOP), 0, 4)

    if chat_times:
        chat = np.zeros(n)
        for t in chat_times:
            if 0 <= t < n * HOP:
                chat[int(t / HOP)] += 1
        chat = _smooth(chat, 4 / HOP)
        score += cfg.w_chat * np.clip(_robust_z(chat, int(300 / HOP)), 0, 4)
    return score


def _peaks(score: np.ndarray, radius: int) -> list[int]:
    idx = []
    for i in range(len(score)):
        lo, hi = max(0, i - radius), min(len(score), i + radius + 1)
        if score[i] > 0 and score[i] == score[lo:hi].max():
            idx.append(i)
    return sorted(idx, key=lambda i: -score[i])


def _window(cfg: Moments, i: int, score: np.ndarray, sents: list[Sentence], words: list[Word], total: float) -> tuple[float, float] | None:
    """Clip edges around the peak at index i.

    The clip opens where the excitement starts (so the first seconds are the hook)
    and closes where it dies down, snapped to sentence boundaries and then fitted
    to the length limits.
    """
    peak = i * HOP
    floor = max(cfg.min_score * 0.5, score[i] * 0.45)
    a = i
    while a > 0 and score[a - 1] >= floor:
        a -= 1
    b = i
    while b < len(score) - 1 and score[b + 1] >= floor:
        b += 1
    onset, offset = a * HOP - cfg.lead_in, (b + 1) * HOP
    spoken = sum(min(w.e, offset) - max(w.s, onset) for w in words if w.e > onset and w.s < offset)
    if not sents or spoken < 0.35 * (offset - onset):
        return _plain_window(cfg, peak, onset, offset, words, total)  # action with little or no talking
    # Sentences whose midpoint falls inside the excited stretch.
    first = next((k for k, s in enumerate(sents) if (s.s + s.e) / 2 >= onset), len(sents) - 1)
    last = max((k for k, s in enumerate(sents) if (s.s + s.e) / 2 <= offset), default=0)
    at_peak = next((k for k, s in enumerate(sents) if s.e >= peak), len(sents) - 1)
    first = min(first, at_peak)
    last = max(last, at_peak, first)
    while sents[last].e - sents[first].s > cfg.max_len and last > first:
        if last > at_peak:
            last -= 1
        else:
            first += 1
    while sents[last].e - sents[first].s < cfg.min_len:
        can_fwd = last < len(sents) - 1 and sents[last + 1].e - sents[first].s <= cfg.max_len
        can_back = first > 0 and sents[last].e - sents[first - 1].s <= cfg.max_len
        if can_fwd:
            last += 1
        elif can_back:
            first -= 1
        else:
            break
    start_word, end_word = sents[first].first, sents[last].last
    # Pad into the silence around the speech without touching neighbouring words.
    prev_end = words[start_word - 1].e if start_word > 0 else 0.0
    next_start = words[end_word + 1].s if end_word < len(words) - 1 else total
    start = max(prev_end, words[start_word].s - 0.25, 0.0)
    end = min(next_start, words[end_word].e + 0.45, total)
    if end - start > cfg.max_len + 1:
        return None
    if end - start < cfg.min_len:  # not enough talk to fill a clip: pad it with the action around it
        return _plain_window(cfg, peak, start, end, words, total)
    return round(start, 2), round(end, 2)


def _plain_window(cfg: Moments, peak: float, onset: float, offset: float, words: list[Word], total: float) -> tuple[float, float] | None:
    """Clip edges for a stretch with little or no speech: the loud stretch itself, fitted to the length limits."""
    start, end = max(0.0, onset), min(total, offset)
    if end - start > cfg.max_len:
        start = max(start, peak - cfg.max_len * 0.4)
        end = min(total, start + cfg.max_len)
    if end - start < cfg.min_len:
        start = max(0.0, start - (cfg.min_len - (end - start)) * 0.4)
        end = min(total, start + cfg.min_len)
        start = max(0.0, end - cfg.min_len)
    for w in words:  # do not cut through a spoken word
        if w.s < start < w.e:
            start = max(0.0, w.s - 0.1)
        if w.s < end < w.e:
            end = min(total, w.e + 0.2)
    if end - start < min(cfg.min_len, 8):
        return None
    return round(start, 2), round(end, 2)


def find(cfg: Moments, wav: Path, words: list[Word], total: float, chat_times: list[float] | None = None, limit: int = 24, cues: list[str] | None = None) -> list[Candidate]:
    """Best non-overlapping clip candidates, strongest first. Works with or without speech."""
    score = score_track(cfg, wav, words, chat_times, cues)
    sents = sentences(words)
    picked: list[Candidate] = []
    for i in _peaks(score, int(8 / HOP)):
        if score[i] < cfg.min_score or len(picked) >= limit:
            break
        peak = i * HOP
        win = _window(cfg, i, score, sents, words, total)
        if not win:
            continue
        start, end = win
        if any(start < c.end + cfg.min_gap and end > c.start - cfg.min_gap for c in picked):
            continue
        a, b = int(start / HOP), max(int(end / HOP), int(start / HOP) + 1)
        inside = score[a:b]
        strength = float(np.sort(inside)[-max(1, len(inside) // 4) :].mean())
        clip_words = [w for w in words if w.s >= start - 0.01 and w.e <= end + 0.01]
        picked.append(Candidate(start, end, round(peak, 2), round(strength, 3), clip_words))
    return sorted(picked, key=lambda c: -c.score)
