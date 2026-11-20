# Clipper

Turns long gameplay or stream footage into short vertical clips with karaoke
captions and schedules them on TikTok, Instagram Reels, YouTube Shorts and
Facebook Reels, several times a day.

```
footage -> transcript -> best moments -> quality checks -> automatic review
        -> titles + hashtags -> vertical render with captions -> posting slots -> report
```

The stages in order, the settings each one runs with, and how this compares
with published clipping systems: `ARCHITECTURE.md`.

## The model

Gameplay-only GTA 6 videos are found online, the highlights are cut out, sorted
into sections and posted as short vertical clips:

1. **Grab.** Each run lists the sources in `config.toml` (YouTube searches for
   no-commentary gameplay, or files in `inbox/`) and filters them by title,
   views and length before anything is downloaded.
2. **Gameplay only.** A video with a streamer on camera or someone talking over
   the game is left out (see below).
3. **Cut.** A long video goes through the moment finder, which reacts to loud
   action, fast talking and the sections' cue words; it works on footage with
   no speech at all. A short clip (up to 62 s) is used whole. A guide of a few
   minutes with no standout moment gives its last stretch.
4. **Sort.** Every clip is filed under a section: new findings, tricks, glitches
   or highlights. It gets that section's badge, hashtags and playlist.
5. **Credit.** The channel the footage came from is named on the clip and in
   every caption, with a link to the original in the YouTube description.
6. **Post.** Four clips a day on four platforms, the sections taking turns.

The sound is the game's own. When characters speak, their lines appear as
karaoke captions; a clip with no speech has none.

Clips are other people's footage unless a source says otherwise. `permission`
on a source records the basis for using it; the daily report lists footage used
with none on file. Each platform decides for itself whether reposted clips are
recommended or paid for.

## Gameplay-only filter

`[filters]` in `config.toml`, on by default. Three layers:

- **Title.** Each online source only takes videos whose title says "no
  commentary" (or similar) and none of "reaction", "live", "stream", ...
- **Facecam.** Sixteen frames are sampled and faces detected (YuNet model,
  bundled). A face that stays in the same spot in 60% of the frames is a
  webcam overlay; characters in the game move with the camera and do not.
- **Talk.** The transcript is searched for things only a commentator says:
  "chat", "this game", "gameplay", "like and subscribe". One hit rejects a
  short clip; a long video needs three hits of two kinds.

What gets left out is listed in the report under "Left out: not gameplay-only".
The talk check cannot catch a commentator with no camera who never uses such
words; the automatic review below is there for those.

## Automatic review

`[review]` in `config.toml`; it runs between `plan` and `execute` when a Claude
API key is saved (in the app under Accounts, or `ANTHROPIC_API_KEY`).

For each planned clip, `plan` writes one image with frames taken evenly across
it (`out/<run>/<id>.jpg`: eight frames for a clip of 40 s or more, four for a
shorter one). The review sends that image, the clip's transcript and the source
video's title to Claude, which answers in a fixed form:

- **keep or drop**, with a reason. Dropped: a streamer seen or heard, story
  cutscenes and spoilers, menus and loading screens, footage that is not the
  game, slurs, gambling or political promotion, personal information.
- **section**: findings, tricks, glitches or moments.
- **title, on-screen headline and a one-line summary**, written from what the
  frames and the transcript show.
- **search tags**: three to five phrases a viewer would type to find the clip.
  They lead the YouTube tags.

The clip is then rendered with that headline and its captions are rebuilt
around that title, so nothing needs editing by hand. The verdicts are in
`plan.json` (`review`), on each clip in the app and in the report under
"Automatic review".

Without a key the run carries on with rule-based titles (the video's own title,
or a line spoken in the clip). If a request fails for one clip, that clip keeps
its rule-based title; if the key is refused, the review stops and the run goes
on. The model is `claude-opus-5-5` with the API's server-side fallback switched
on; at a dozen clips a day the cost is tens of cents.

## Search check

Every caption names the game and what people search for in that section
(`keyword` in `[[sections]]`): "Police car launches off the pier | GTA 6 Glitch".
The game's main hashtag (the first in `seo.hashtags`) is on every clip, followed
by the section's and a rotating pick from the rest.

Before a clip can be posted, `metadata.proof` checks its text: the title says
what happens, is not cut short, shouted or over 70 characters; the YouTube title
names the game, ends in #shorts and fits in 100 characters; the description
credits the source and carries 1 to 15 hashtags; there are at least three tags
within YouTube's 500 characters; each caption names the game, credits the
source, has 3 to 10 hashtags (30 on Instagram) without repeats, and uses no
banned term. A title that a finished clip already carries is flagged too:
without the automatic review, clips cut from one video can end up with the
same title. The result is on each clip in the app. With automatic posting on,
a clip that fails is made but held for a look instead of posted. Editing a
clip's text in the app runs the check again.

## System check

```bash
python3 -m clipper check
```

or "Check everything" on the Activity tab of the app. It tries each part on the
computer it runs on, for real, and says part by part whether it works:

| Part | What is tried |
|---|---|
| Tools on this computer | ffmpeg, the JavaScript runtime, the caption font, the speech model |
| Finding videos | The first online source is asked what it lists; the title, views and length rules are applied |
| Downloading a video | The first 15 seconds of one listed video are downloaded |
| Cutting, captions and rendering | A short test video is transcribed, captioned and rendered as a vertical clip |
| Automatic review | One real request to Claude must come back in the expected form (needs the key; a cent or two) |
| Posting connection | The posting service accepts the key and the chosen profile has every account connected |

Nothing is posted and nothing is added to the ledger. A part with no key yet is
reported as not set up rather than as a failure.

## The app

```bash
python3 -m clipper ui
```

A page in your browser, served from this computer only: the finished clips with
a player, editable titles and captions, post now or at the next free time, a
"Make clips now" button with live progress, settings, the connection to the
posting service and the key for the automatic review. On Windows the installer
adds a desktop shortcut for it.

Posting goes through Upload-Post: one key covers TikTok, Instagram, YouTube and
Facebook. Direct connections to each platform are not built in, because uploads
from a private app are locked to private on YouTube and TikTok until the app
passes their audits.

## Quick start

```bash
python3 -m clipper setup --install
```

(`./setup.sh` does the same. To run on your own computer, see `LOCAL.md`.)

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
| `check` | Tries each part for real: finding videos, downloading, cutting, the review, the posting connection |
| `clip VIDEO` | One-off: finished clips from a file into `out/manual/` |
| `plan` | Collects footage, finds moments, writes `out/<run>/plan.json` |
| `review` | Has Claude check the planned clips, drop the unsuitable ones and write the titles |
| `execute` | Renders the planned clips and schedules them |
| `run` | `plan`, `review` (when a key is saved), `execute` |
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
- **Metadata** (`metadata.py`): without the automatic review, the title is the
  video's own title or the punchiest sentence in the clip; with it, the title
  the review wrote. The captions for each platform are built around it.
  When `campaign.paid = true` every caption starts with `#ad`, the video carries
  an on-screen paid label, and the platforms' own paid-content flag is set.
- **Schedule** (`schedule.py`): one clip per slot in `schedule.slots`; extra good
  clips fill the next `max_days_ahead` days.
- **Ledger** (`state/ledger.json`): what was processed and posted. It is
  committed after every run so nothing is clipped or posted twice.
- **Report** (`reports/latest.md`): what is scheduled, what failed, and what
  was left out. Next to every finished clip in `out/<run>/` is a `.txt` with its
  title, captions and hashtags for each platform, ready to paste.

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
home connection: `./run_daily.sh` on your own computer, or a Claude session
that runs there.

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

The review tests run against a local stand-in for the Claude API in the same
way: they pin down what is sent and how each kind of answer is handled.

`tests/test_system.py` runs the whole system as one. It presses "Make clips now"
on the running app, which starts a real run: the downloader fetches a generated
five-minute video from a local web server, the speech model transcribes it, two
clips of about 1:30 are found, reviewed, rendered and saved with their text; the
app then schedules them and the test reads the upload requests. A second test
does the same with automatic posting on, in one command. The video site, the
Claude API and the posting service are local stand-ins; everything else is the
real code path.

## Not yet verified against live services

- The automatic review against the real Claude API: no key was available where
  this was built, so no real clip has been judged or titled by it yet.

- Uploading through a real Upload-Post account, and the exact shape of its
  status and analytics responses (links and views are extracted defensively).
- Downloading from YouTube: refused by its bot check from the cloud machine
  this was built on (finding videos there works). `python3 -m clipper check` on
  a home connection tries both.
- The facecam check was run on seven real streamer clips (all caught) and on the
  same clips with the webcam cropped out (all passed). It has not been run on
  no-commentary walkthrough footage.
- The moment finder on long real gameplay; loud-action detection is tested on
  generated audio only.

## Font

Captions use Montserrat ExtraBold, bundled under the SIL Open Font License
(`assets/fonts/OFL.txt`).
