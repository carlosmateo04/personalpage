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

## Claude, built in

`[ai]` in `config.toml`; `clipper/claude.py`, `signin.py`, `scout.py`,
`review.py`. Claude is reached by signing in with a Claude Console account, with
an Anthropic API key, or through Amazon Bedrock with an AWS profile. Several
accounts can be saved in the app under Accounts and switched between there or
from the top of any tab (see [ACCOUNTS.md](ACCOUNTS.md)). The sign-in is done by
Anthropic's own tool (`ant`), and the SDK reads and renews what it stores; keys
are kept in the credential store, one per account. Claude does two jobs in every
run, and a third when asked:

**Scout: picking the moments.** For each long video Claude is shown the whole
video as sheets of small frames, each with its time written on it (one frame
every 5 to 20 seconds, up to `ai.max_frames`), what is said with times, where
the sound stands out, and the stretches the loudness finder would clip. It
answers with the moments to clip, each with start and end, section, title,
headline, a reason and a score, or says that the video cannot be used (a
streamer on camera, not gameplay). Its picks are fitted to the clip length,
kept apart, and then go through the same checks as any candidate.

**Editor: checking and editing each clip.** For each planned clip Claude is
shown frames from a little before it to a little after it, with their times,
and what is said. It answers in a fixed form:

- **keep or drop**, with a reason. Dropped: a streamer seen or heard, story
  cutscenes and spoilers, menus and loading screens, footage that is not the
  game, slurs, gambling or political promotion, personal information.
- **the cut**: it may move each end by up to `ai.margin` seconds so the clip
  opens where the action starts; the length limits still hold.
- **section, title, on-screen headline, one-line summary and search tags**.
- **on-screen notes**: up to three short notes at given moments, only where
  nobody is speaking.
- **the cover frame** and **a score** from 0 to 100.

The clip is rendered with all of that. **Editing again:** a finished clip can
be handed back to Claude from the app with an instruction ("start later", "a
calmer title"); it is cut and rendered anew.

Without Claude the run carries on with the loudness finder and rule-based
titles. If one request fails, that video or clip falls back to the rules; if
the sign-in is refused, Claude is left out for the rest of the run. The model
is `claude-opus-5-5`; a request the model declines is re-run on another model
(by the API itself, or from here on Bedrock). Requests and an estimated cost
per month are counted in `state/ai_usage.json` and shown in the app.

## The cut each platform gets

`[edits]` in `config.toml`; `clipper/variants.py`. A clip is made once. Each
platform then gets the version its rules ask for: TikTok only clips over a
minute (what its Creator Rewards Program counts), YouTube at most 59 seconds (a
longer Short is blocked when a copyright owner claims something in it),
Facebook at most 90 seconds (where Page reels stop), Instagram at most three
minutes. Where a clip is longer than a platform's maximum, a second version is
rendered around the moment the clip builds to, never cutting through a spoken
word, with its own captions and cover; platforms with the same cut share one
file. The outbox hands each connector its platform's version. The numbers are
changeable under Settings. They are format rules: whether a channel is paid
also depends on the platform accepting the videos as original, which no edit
settles (see ACCOUNTS.md).

## Rules for being paid

`[monetize]` in `config.toml`; `clipper/monetize.py`, which lists every rule,
whose it is and what is done about it. Beyond length, the platforms' rules are
built into each clip:

- **A calm opening.** Claude marks graphic moments; the clip's start, and the
  start of each platform's own version, is moved so that none falls in the
  first 15 seconds (YouTube pays nothing for one in the first 7, and less for
  one in seconds 8 to 15). The cover frame is never taken from one.
- **Dropped outright:** clips showing what no platform pays for at any point,
  or carrying another platform's or channel's watermark, logo or QR code.
- **Strong language** is silenced in the sound, as it is masked in the text.
- **Music** is heard by a small sound-recognition model on this computer and
  reported with how long it plays; a setting holds such clips back.
- **A still or blurry picture** is measured on the source.
- **Hashtags** are capped at five for Instagram and Facebook, and captions are
  checked for requests for likes and follows.

Each finished clip then carries a check per platform (`money` in the ledger,
"Monetization check" on its card): met, at risk or not checkable, or broken.
With `strict`, a platform whose rule a clip breaks is not sent it. The one
rule no edit meets, originality, is reported on every clip of another
channel's footage.

## The business around the clips

The Business tab and further Settings panels; described for the owner in
[ACCOUNTS.md](ACCOUNTS.md) under "Running it as a business". Each part is a
small module that keeps its own JSON file in `state/`:

| Part | Module | What it does |
|---|---|---|
| Going live | `launch.py` | The steps to a live channel, each read off what is really there |
| Commentary | `pipeline.set_voiceover`, `ui.html` | The owner's voice recorded in the app and put on every version of a clip; Claude drafts what to say |
| Own gameplay | `settings.overlay`, `intake._folder` | Capture folders as sources of own footage; a file still recording waits |
| Money | `money.py` | Income and costs, typed in or worked out; a sheet per year |
| Progress | `progress.py` | Followers and views against each programme's bar |
| What works | `learn.py` | Views weight the ranking of candidates and are told to Claude |
| Campaign desk | `desk.py` | The status and payout of each post owed to a campaign |
| Rights | `rights.py` | Permissions on file; claims and strikes, which block a source |
| Comments | `audience.py` | Unanswered comments with replies drafted by Claude; sent only by hand |
| Alerts | `alerts.py` | Messages to the owner's phone through ntfy |
| Phone access | `ui.py` (`allowed`, `page_key_ok`) | The app from a phone on the home network, with a private key |
| Safety | `upkeep.py` | Daily backups and restore, an update check, the downloader kept current |
| Channels | `channels.py` | Further channels, each a data folder of its own |

## Posting

`clipper/outbox.py` and `clipper/connectors/`. This is the clipper's own
version of what a posting service does; see [ACCOUNTS.md](ACCOUNTS.md) for what
each platform allows and how to connect it.

- **Connectors** talk to each platform's own API with a developer app the user
  registered: YouTube (resumable upload, scheduled by YouTube), TikTok (a draft
  to the inbox, a private post, or a public post with per-clip audience and
  interaction choices once TikTok has audited the app), Facebook (Page reels up to 90 seconds, Page
  videos beyond, scheduled by Facebook) and Instagram (reels from a local
  file). Sign-in is each platform's OAuth flow, coming back to the app on this
  computer.
- **The outbox** keeps one post per platform on each clip
  (`posts` in the ledger: waiting, sending, scheduled, processing, posted,
  draft, failed). `deliver` sends what is due: platforms that schedule
  themselves get the clip at once with its time, the others when their time
  comes. Failures worth retrying are tried again with a growing wait; an
  interrupted send is never repeated blindly; nothing is sent twice.
- **Who delivers:** the run at its end, the app every minute while it is open,
  and `python3 -m clipper post` (the Windows task "Clipper posting" runs it
  every 15 minutes).
- **The posting service** (Upload-Post) remains a route per platform: used for
  platforms not connected directly, or listed in `publish.prefer_service`.
- **By hand:** a clip posted on a platform by the user (the app copies the
  caption, shows the file and opens TikTok's upload page) is marked as posted
  there, so it is not sent a second time; the other platforms still go out.
- **TikTok's audit is not worked around.** The connector uses TikTok's Content
  Posting API and nothing else: no imitation of the TikTok app, no automated
  browser on a signed-in session.

## Story playthroughs and cutscenes are left out

Found by running on real GTA 6 footage: most of what a "no commentary" search
returns is full story walkthroughs, hours long, and the loudest moments in them
are cutscenes, which give the plot away. Two things keep them out:

- The searches skip titles that say "walkthrough", "full game", "mission",
  "ending", "cutscene" or "movie" (`title_none` in each source).
- A passage is checked for the black bars the game puts above and below a
  cutscene (`filters.no_cutscenes`). A clip with bars in 40% of its frames or
  more is left out; the report says so.

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
| `check` | Tries each part for real: finding videos, downloading, cutting, Claude, the posting accounts |
| `post` | Sends the posts that are due and reads back what became of the ones that went out |
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

Each part has its own tests against a local stand-in that answers the way the
service's documentation says and records what it was sent: `test_claude.py`
(API key, Bedrock, a declined request, errors, cost), `test_scout.py`,
`test_review.py`, `test_youtube.py`, `test_tiktok.py`, `test_meta.py`
(sign-in, upload, resuming an interrupted upload, scheduling, refusals, status),
`test_outbox.py` (queueing, timing, retries, never sending twice),
`test_state.py` (several processes on one ledger), `test_accounts.py` (the
Accounts tab end to end), `test_signin.py` and `test_claude_accounts.py`
(signing in with Claude through a stand-in for Anthropic's sign-in tool, whose
stored sign-in the real SDK then reads; adding, switching and removing
accounts), `test_variants.py` (the cut each platform gets), `test_monetize.py`
(the rules for being paid: the rendered sound really is silent where strong
language is spoken, music and still pictures are told apart on generated
video, the check per platform, the editor's drops and moved openings),
`test_business.py` (money, rights, what works, the campaign desk, progress,
backups and restore, updates, alerts, channels, capture folders, going live)
and `test_audience.py` (followers, earnings and comments against the
stand-ins for Google and Meta).

`tests/test_system.py` runs the whole system as one. The four accounts and
Claude are connected through the app; "Make clips now" starts a real run that
downloads a generated five-minute video from a local web server, has the scout
pick two moments and the editor check and edit them, and renders two clips of
about 1:30, each with a 59-second version for YouTube; one is edited again by
Claude with an instruction; "Schedule all" hands the clips to YouTube (its
short version) and Facebook with their posting times; when the time comes
TikTok and Instagram are sent the full clip; then the platforms' answers are
read back until every post is out, and the TikTok drafts are marked as posted
by hand. A second test does it hands-off with one command, with TikTok set to
post publicly with the caption. The video site, the Claude API, Google, TikTok and Meta are local
stand-ins; everything else is the real code path.

## Not yet verified against live services

Everything above runs against stand-ins built from the documentation. Against
the real services, as of this writing:

- **Claude:** no request has been made with a real key, so what the model picks
  and writes on real footage has not been seen. `python3 -m clipper check`
  makes one real request.
- **Sign in with Claude:** tested against a stand-in for Anthropic's sign-in
  tool written from its documentation. The real tool's sign-in needs a person
  and a Console account; the first sign-in on the user's computer is the test.
  The Windows test machine checks that the installer fetches the real tool and
  that it has the commands the app runs.
- **The rules for being paid:** read from the platforms' pages through web
  search and press reports, because those pages could not be opened from the
  machine this was built on; YouTube's seconds for graphic violence come from
  press reports of its September 2026 update. Whether Claude spots a graphic
  moment or a watermark in real footage has not been seen with a real key.
  The music listener was run on two real GTA 6 videos and heard music in both.
- **Followers, earnings and comments:** written from the platforms' API
  references as remembered, not re-read (their pages cannot be opened from the
  machine this was built on), and run against stand-ins only. The further
  permissions they need (YouTube `youtube.force-ssl` and
  `yt-analytics-monetary.readonly`; Meta `instagram_manage_comments`,
  `pages_manage_engagement`, `pages_read_user_content`) may need app review at
  Meta beyond the owner's own accounts.
- **Alerts:** the message format follows ntfy's documentation; no message has
  been sent to a real phone.
- **Phone access and recording:** the access rules are unit-tested; recording
  was driven in a real browser with a simulated microphone and ended up on the
  clip. A real phone on a real home network has not been tried.
- **Updating from the app:** the update script has not been run on Windows by
  a person; the Windows test checks that it is installed and that the version
  is noted.
- **TikTok public posts:** refused by TikTok until it has audited the app,
  which it does not do for tools posting to their owner's accounts; the code
  path is exercised against the stand-in only.
- **YouTube, TikTok, Facebook, Instagram:** no real sign-in or upload has been
  made; that needs the developer apps and accounts. The first connection of
  each is the test. For Meta, whether posts are visible to everyone has to be
  looked at after the first post (see ACCOUNTS.md).
- **Upload-Post:** request shapes follow its SDK; no real upload has been made.
- **Downloading from YouTube:** refused by its bot check from the cloud machine
  this was built on (finding videos there works). On a home connection one
  video has been fetched. `check` tries both.
- **Real footage:** two real GTA 6 videos were clipped end to end with the
  rule-based finder (which is how the cutscene check and the story-playthrough
  rule came about); not yet with Claude doing the picking.

## Font

Captions use Montserrat ExtraBold, bundled under the SIL Open Font License
(`assets/fonts/OFL.txt`).
