# Clipper

Turns long gameplay or stream footage into short vertical clips with karaoke
captions and schedules them on TikTok, Instagram Reels, YouTube Shorts and
Facebook Reels, several times a day.

```
footage -> transcript -> best moments -> quality checks -> titles + hashtags
        -> vertical render with captions -> posting slots -> report
```

## Quick start

```bash
./setup.sh
```

```bash
python3 -m clipper doctor
```

Make clips from one video, without posting anything:

```bash
python3 -m clipper clip path/to/video.mp4 --count 5
```

The full daily run (reads `config.toml`, posts nothing while `publish.mode = "dry_run"`):

```bash
./run_daily.sh
```

## Commands

| Command | What it does |
|---|---|
| `doctor` | Checks ffmpeg, the speech models, the font, the config and the posting service |
| `clip VIDEO` | One-off: finished clips from a file into `out/manual/` |
| `plan` | Collects footage, finds moments, writes `out/<run>/plan.json` |
| `execute` | Renders the planned clips and schedules them |
| `run` | `plan` followed by `execute` |
| `approve [IDS] [--voiceover ID=FILE]` | Schedules clips held for review, optionally mixing in a voiceover |
| `status` | Pulls post links and view counts, rewrites the report |
| `report` | Prints the report |

`plan.json` can be edited between `plan` and `execute`: change a clip's
`meta.title` or `meta.hook`, set `"drop": true`, or point `"voiceover"` at an
audio file to mix your own commentary over the clip.

## How the pieces work

- **Transcription** (`transcribe.py`): voice detection splits the audio, then the
  Parakeet speech model (CPU, via sherpa-onnx) returns every word with its timing.
  Roughly 7x real time on four cores.
- **Moments** (`moments.py`): each half second is scored on loudness against the
  surrounding minutes, talking speed, hype words, and chat speed when a chat log
  (`<video>.chat.json`) sits next to the video. Peaks become 18-45 s clips that
  open where the excitement starts and are cut on sentence boundaries.
- **Captions** (`captions.py`): two or three words on screen at a time; the word
  being spoken is highlighted. `captions.mode = "sweep"` fills the colour across
  the line instead.
- **Render** (`render.py`): 1080x1920, 30 fps. Layouts: `blur` (whole picture
  over a blurred background), `crop` (fill the screen), `stack` (facecam above
  gameplay). Audio is normalised to about -14 LUFS.
- **Quality checks** (`gates.py`): rejects clips that are too short, mostly
  silent, overlap a clip already made, repeat a moment from another video, or
  mention a banned term (gambling sponsors and political terms by default).
- **Metadata** (`metadata.py`): the title is the punchiest sentence in the clip.
  When `campaign.paid = true` every caption starts with `#ad`, the video carries
  an on-screen paid label, and the platforms' own paid-content flag is set.
- **Schedule** (`schedule.py`): one clip per slot in `schedule.slots`; extra good
  clips fill the next `max_days_ahead` days.
- **Ledger** (`state/ledger.json`): what was processed and posted. It is
  committed after every run so nothing is clipped or posted twice.
- **Report** (`reports/latest.md`): what is scheduled, what failed, and the post
  links to submit to the campaign.

## Footage sources

| Type | Use |
|---|---|
| `folder` | Video files dropped into `inbox/` |
| `urls` | Direct video links, one per line in `sources/urls.txt` |
| `ytdlp` | A channel's videos page, a playlist or a single video, fetched with yt-dlp |

Every `urls` and `ytdlp` source must state `permission`: the campaign or other
reason the footage may be used. Only clip footage you own or have been given
permission to clip. YouTube's terms prohibit downloading without permission and
its downloads are unreliable from cloud servers.

## Going live

1. Create the TikTok, Instagram, YouTube and Facebook accounts.
2. Sign up at upload-post.com, create a profile and connect the four accounts.
3. Put the profile name in `publish.profile`. Provide the API key as
   `UPLOAD_POST_API_KEY`, or as a network secret for `api.upload-post.com`
   (header `Authorization`, prefix `Apikey`) in a Claude cloud environment.
4. `python3 -m clipper doctor` must report the profile as found.
5. Set `publish.mode = "live"`.

For the first live day, consider `tiktok_post_mode = "MEDIA_UPLOAD"` (clips land
in your TikTok drafts) and `hold_for_review = true`.

## Tests

```bash
python3 -m pytest -q tests
```

The posting tests run against a local stand-in for the Upload-Post API. They pin
down the requests this code sends; the stand-in's responses are assumptions.

## Not yet verified against live services

- Uploading through a real Upload-Post account, and the exact shape of its
  status and analytics responses (links and views are extracted defensively).
- yt-dlp against Twitch, Kick or YouTube (tested only against a local server).
- Moment finding on real GTA 6 stream footage; the thresholds in `[moments]`
  were tuned on synthetic test footage and will need adjusting.

## Font

Captions use Montserrat ExtraBold, bundled under the SIL Open Font License
(`assets/fonts/OFL.txt`).
