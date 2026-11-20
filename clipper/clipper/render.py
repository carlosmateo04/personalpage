"""Cut a clip, reframe it to vertical, burn in the captions, normalise the audio."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .media import run


def _esc(path: Path | str) -> str:
    """Escape a path for use inside an ffmpeg filter argument.

    Forward slashes are used on every system, so a Windows path becomes
    'C\\:/Users/...': only the drive colon needs escaping.
    """
    s = Path(path).as_posix().replace(":", "\\:").replace("'", "\\'")
    return f"'{s}'"


def _even(x: float) -> int:
    return int(x) // 2 * 2


def video_graph(cfg: Config, ass: Path, duration: float = 0.0) -> str:
    r = cfg.render
    W, H = r.width, r.height
    if r.layout == "blur":
        # Blurred, darkened copy fills the frame; the zoomed picture sits on top of it.
        graph = (
            f"[0:v]fps={r.fps},split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
            f"boxblur=10:2,scale={W}:{H},eq=brightness=-0.14:saturation=0.85[bg];"
            f"[fgsrc]crop=w=trunc(iw/{r.zoom}/2)*2:h=ih:x=(iw-iw/{r.zoom})/2:y=0,scale={W}:-2[fg];"
            f"[bg][fg]overlay=x=0:y=max(0\\,trunc(H*{r.picture_y}-h/2))[base]"
        )
    elif r.layout == "crop":
        graph = (
            f"[0:v]fps={r.fps},scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:(iw-{W})*{r.crop_x}:(ih-{H})/2[base]"
        )
    else:  # stack: facecam on top, gameplay below
        fx, fy, fw, fh = r.facecam
        top = _even(H * r.facecam_share)
        bottom = H - top
        graph = (
            f"[0:v]fps={r.fps},split=2[camsrc][gamesrc];"
            f"[camsrc]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},scale={W}:{top}:force_original_aspect_ratio=increase,crop={W}:{top}[cam];"
            f"[gamesrc]scale={W}:{bottom}:force_original_aspect_ratio=increase,crop={W}:{bottom}[game];"
            f"[cam][game]vstack[base]"
        )
    graph += f";[base]ass={_esc(ass)}:fontsdir={_esc(cfg.fonts_dir)}"
    if r.progress_bar and duration > 0:
        # A thin bar across the top that slides in from the left and is full when the clip ends.
        colour = "0x" + cfg.captions.active_color.lstrip("#")
        graph += (
            f"[texted];color=c={colour}:s={W}x10:r={r.fps}:d={duration:.3f}[bar];"
            f"[texted][bar]overlay=x='-W+W*t/{duration:.3f}':y=0:shortest=1"
        )
    return graph + ",setsar=1,format=yuv420p[v]"  # square pixels: scaling can leave them a hair off


def audio_graph(cfg: Config, voiceover: bool, mute: list[tuple[float, float]] | None = None) -> str:
    norm = f"loudnorm=I={cfg.render.loudness}:TP=-1.5:LRA=11,aresample=48000"
    if mute:  # the clip's own sound is silenced during these stretches (strong language)
        when = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in mute)
        norm = f"volume=volume=0:enable='{when}'," + norm
    if not voiceover:
        return f"[0:a]{norm}[a]"
    # The clip's own audio ducks under the voiceover while it is speaking.
    return (
        f"[0:a]{norm}[orig];"
        f"[1:a]aresample=48000,loudnorm=I=-13:TP=-1.5:LRA=11,adelay=300:all=1,asplit=2[vo][key];"
        f"[orig][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450[ducked];"
        f"[ducked][vo]amix=inputs=2:duration=first:normalize=0[a]"
    )


def render(cfg: Config, src: Path, start: float, end: float, ass: Path, out: Path, voiceover: Path | None = None,
           mute: list[tuple[float, float]] | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = cfg.render
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src)]
    if voiceover:
        cmd += ["-i", str(voiceover)]
    cmd += [
        "-filter_complex", video_graph(cfg, ass, end - start) + ";" + audio_graph(cfg, voiceover is not None, mute),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", r.preset, "-crf", str(r.crf), "-r", str(r.fps), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart", str(out),
    ]
    run(cmd, "render")
    return out
