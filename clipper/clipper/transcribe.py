"""Speech to text with a start and end time for every word.

Voice activity detection splits the audio into speech runs; each run is decoded
with the NVIDIA Parakeet model through sherpa-onnx (CPU only, about 10x real time
on four cores). The result is cached next to the audio.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Config

ASR_DIR = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
VAD_FILE = "silero_vad.onnx"
RATE = 16000
_PUNCT = set(".,!?;:")


@dataclass
class Word:
    w: str
    s: float
    e: float


class TranscribeError(RuntimeError):
    pass


def models_present(cfg: Config) -> bool:
    d = cfg.models_dir
    return (d / ASR_DIR / "encoder.int8.onnx").exists() and (d / VAD_FILE).exists()


def _recognizer(cfg: Config):
    import sherpa_onnx

    d = cfg.models_dir / ASR_DIR
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(d / "encoder.int8.onnx"),
        decoder=str(d / "decoder.int8.onnx"),
        joiner=str(d / "joiner.int8.onnx"),
        tokens=str(d / "tokens.txt"),
        model_type="nemo_transducer",
        num_threads=cfg.transcribe.threads,
    )


def _speech_runs(cfg: Config, audio: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """(start sample, samples) for every stretch of speech."""
    import sherpa_onnx

    vc = sherpa_onnx.VadModelConfig()
    vc.silero_vad.model = str(cfg.models_dir / VAD_FILE)
    vc.silero_vad.threshold = cfg.transcribe.vad_threshold
    vc.silero_vad.min_silence_duration = 0.3
    vc.silero_vad.min_speech_duration = 0.2
    vc.silero_vad.max_speech_duration = cfg.transcribe.max_segment
    vc.sample_rate = RATE
    vad = sherpa_onnx.VoiceActivityDetector(vc, buffer_size_in_seconds=60)
    window = vc.silero_vad.window_size
    runs: list[tuple[int, np.ndarray]] = []

    def drain() -> None:
        while not vad.empty():
            seg = vad.front
            runs.append((int(seg.start), np.asarray(seg.samples, dtype=np.float32)))
            vad.pop()

    for i in range(0, len(audio) - window + 1, window):
        vad.accept_waveform(audio[i : i + window])
        drain()
    vad.flush()
    drain()
    return runs


def words_from_tokens(tokens: list[str], starts: list[float], durations: list[float], offset: float) -> list[Word]:
    """Join sub-word tokens into words. A token that begins with a space starts a new word."""
    words: list[Word] = []
    pending = True  # the next token starts a new word
    for i, tok in enumerate(tokens):
        begin = offset + starts[i]
        dur = durations[i] if i < len(durations) and durations[i] > 0 else 0.08
        end = begin + dur
        text = tok.replace("▁", " ")
        fresh = pending or text.startswith(" ")
        text = text.strip()
        if not text:
            pending = True  # a bare space token: the word break belongs to the next token
            continue
        pending = False
        if fresh and not all(c in _PUNCT for c in text):
            words.append(Word(text, begin, end))
        elif words:
            words[-1].w += text
            if not all(c in _PUNCT for c in text):
                words[-1].e = end
    for a, b in zip(words, words[1:]):
        a.e = max(a.s + 0.05, min(a.e, b.s))
    for w in words:
        w.s, w.e = round(w.s, 3), round(max(w.e, w.s + 0.05), 3)
    return words


def transcribe(cfg: Config, wav: Path, cache: Path | None = None) -> list[Word]:
    """Words with timings for a 16 kHz mono WAV."""
    if cache and cache.exists():
        return [Word(**w) for w in json.loads(cache.read_text())["words"]]
    if not models_present(cfg):
        raise TranscribeError(f"speech models missing in {cfg.models_dir}; run ./setup.sh")
    audio, rate = sf.read(str(wav), dtype="float32", always_2d=True)
    if rate != RATE:
        raise TranscribeError(f"{wav.name}: expected {RATE} Hz audio, got {rate}")
    audio = np.ascontiguousarray(audio[:, 0])
    rec = _recognizer(cfg)
    words: list[Word] = []
    runs = _speech_runs(cfg, audio)
    for i in range(0, len(runs), 8):
        batch = runs[i : i + 8]
        streams = []
        for _, samples in batch:
            st = rec.create_stream()
            st.accept_waveform(RATE, samples)
            streams.append(st)
        rec.decode_streams(streams)
        for (start, _), st in zip(batch, streams):
            r = st.result
            words.extend(words_from_tokens(list(r.tokens), list(r.timestamps), list(getattr(r, "durations", []) or []), start / RATE))
    words.sort(key=lambda w: w.s)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"words": [asdict(w) for w in words]}))
    return words
