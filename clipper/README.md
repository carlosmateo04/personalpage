# Clipper

Turns long gameplay or stream footage into short vertical clips with karaoke
captions and schedules them on TikTok, Instagram Reels, YouTube Shorts and
Facebook Reels, several times a day.

```
footage -> transcript -> best moments -> quality checks -> titles + hashtags
        -> vertical render with captions -> posting slots -> report
```

## The model

GTA 6 videos and clips are found online, the highlights are cut out, sorted into
sections and posted as short vertical clips:

1. **Grab.** Each run lists the sources in `config.toml`: a Twitch channel's
   most-viewed clips of the last 24 hours, a YouTube search, a channel's videos,
   or files in `inbox/`. Videos are filtered by views, length and game before
   anything is downloaded.
2. **Cut.** A short clip (up to 62 s, such as a Twitch clip) is used whole. A long
   video goes through the moment finder, which reacts to loudness, fast talking,
   hype words and the sections' cue words.
3. **Sort.** Every clip is filed under a section: new findings, tricks, glitches
   or wild moments. It gets that section's badge, hashtags and playlist.
4. **Credit.** The channel the footage came from is named on the clip and in
   every caption, with a link to the original in the YouTube description.
5. **Post.** Four clips a day on four platforms, the sections taking turns.

Clips are other people's footage unless a source says otherwise. `permission`
on a source records the basis for using it; the daily report lists footage used
with none on file. Each platform decides for itself whether reposted clips are
recommended or paid for.

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
./run_daily.sh --commit
```

`--commit` saves the ledger and report to git after the run; leave it off to commit them yourself.

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
`meta.title` or `meta.hook`, or set `"drop": true`. Voiceover is optional and
off: only if you point `"voiceover"` at a recording of your own is it mixed in.

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
| `ytdlp` | Anything yt-dlp can list: a Twitch channel's top clips, a channel's videos, a playlist, one video, or a YouTube search link |
| `folder` | Video files dropped into `inbox/` |
| `urls` | Direct video links, one per line in `sources/urls.txt` |

Per source: `category` (only this game), `min_views`, `min_minutes` /
`max_minutes`, `max_items` (videos taken per run), `section` (where its clips
go by default) and `permission`.

YouTube refuses automated downloads from cloud machines ("confirm you're not a
bot"). The run records those videos as not fetched and moves on; it does not
try to get around the check. YouTube sources work when the run happens on a
home connection. Twitch works from cloud machines.

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
- Downloading from YouTube (refused by its bot check from this machine) and
  from Kick (not tried). Twitch clips were listed, downloaded and clipped live.
- The moment finder on long real videos; so far only whole Twitch clips have
  gone through on real footage.
- Section sorting on real findings, tricks and glitch videos: launch-day Twitch
  clips all landed in "moments".

## Font

Captions use Montserrat ExtraBold, bundled under the SIL Open Font License
(`assets/fonts/OFL.txt`).
