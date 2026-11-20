# Running the clipper on your own computer

YouTube refuses video downloads from cloud machines, so the daily run has to
happen on a home internet connection. Everything else is the same.

It is tested on Linux and on Windows. macOS should behave like Linux but has not been run.

## What the computer needs

On Windows the one-line installer below brings everything itself. Otherwise:

- Python 3.11 or newer
- ffmpeg (macOS: `brew install ffmpeg`; Windows: `winget install ffmpeg`; Linux: `apt-get install -y ffmpeg`)
- Deno 2.3 or newer, or Node 22 or newer: the YouTube downloader needs a JavaScript runtime
  (macOS: `brew install deno`; Windows: `winget install DenoLand.Deno`; Linux: `curl -fsSL https://deno.land/install.sh | sh`)
- git, and about 2 GB of free disk space

## Windows: one line

Paste this into PowerShell:

```bash
irm https://raw.githubusercontent.com/carlosmateo04/personalpage/refs/heads/claude/quirky-ramanujan-pk7f77/clipper/install.ps1 | iex
```

It needs no administrator rights and no other software. It puts everything in
one folder, `clipper-run` in your user profile (Python, ffmpeg, the JavaScript
runtime, the clipper, the speech model), adds a **Clipper** shortcut to your
desktop, and opens the app in your browser.

In the app:

- **Make clips now** finds and downloads this week's gameplay-only GTA 6 videos
  and cuts the clips. Progress shows at the top and under Activity.
- **Clips** shows each finished clip with a player. Edit its title and caption,
  then post it now, send it to the next free posting time, or drop it.
- **Accounts** sets up Claude (sign in with a Claude Console account, an API
  key, or Amazon Bedrock; several accounts can be saved, and the one in use is
  switched there or from the "Claude" chip at the top), connects
  YouTube, TikTok, Facebook and Instagram each with its own sign-in, optionally
  the posting service, and switches the daily run on or off. What each platform
  allows and the steps are in [ACCOUNTS.md](ACCOUNTS.md).
- **Business** shows the way to going live, progress to being paid, money in
  and out, what works, the campaign desk, rights and comments. See
  [ACCOUNTS.md](ACCOUNTS.md), "Running it as a business".
- With Claude set up, it picks the moments in each video, checks every clip,
  moves the cut to where the action starts and writes the title, headline and
  notes. "Edit again with Claude" on a clip takes an instruction in your own
  words. Without Claude, Clipper uses its loudness finder and simpler titles.
- Each clip shows where it stands on every platform (waiting, scheduled,
  posted, draft, failed) with links and views, and "Try again" or "Take back"
  where that applies.
- **Activity** shows the current run and the last report, and has **Check
  everything**: it tries each part on this computer (finding videos,
  downloading, cutting and captions, Claude, the posting accounts) and shows which work. Use it after installing and after adding a
  key.
- **Settings** holds clip length, posting times, searches and the rest. What
  you change there is kept in `clipper-run\data\settings.json` and survives
  updates.

Afterwards:

- Open the app with the desktop shortcut. Leave its small window open while you
  use it; closing that window quits Clipper.
- Pasting the install line again updates the clipper and keeps your data.
- Deleting the `clipper-run` folder (and the shortcut) removes everything.
- `run.cmd` in `clipper-run` makes clips without the app.

The installer and the clipper's tests run on a Windows machine on every change
(`.github/workflows/clipper-windows-test.yml`), using generated footage.

## One-time setup on macOS or Linux

```bash
git clone https://github.com/carlosmateo04/personalpage.git
```

```bash
cd personalpage && git checkout claude/quirky-ramanujan-pk7f77 && cd clipper
```

```bash
python3 -m venv .venv && source .venv/bin/activate
```

Install the packages and download the speech model (about 480 MB):

```bash
python -m clipper setup --install
```

Check that everything is in place:

```bash
python -m clipper doctor
```

## The daily run

With the virtual environment active, from the `clipper` folder:

```bash
python -m clipper status
```

```bash
python -m clipper plan
```

With a Claude API key saved, let it check the plan and write the titles:

```bash
python -m clipper review
```

(Without a key, review `out/<newest run>/plan.json` yourself; see below.) Then:

```bash
python -m clipper execute
```

`python -m clipper run` does all three in one go.

While `publish.mode = "dry_run"` in `config.toml` nothing is posted. The finished
clips are in `out/<run>/`: each `.mp4` has a `.txt` next to it with the title,
caption and hashtags for TikTok, Instagram, Facebook and YouTube, ready to paste.

## Letting Claude do it

Open a Claude Code session on this computer in the `personalpage` folder (the
Claude desktop app, or `claude` in a terminal there) and say:

> Read clipper/LOCAL.md and do the clipper's daily run.

### Instructions for that session

1. Make sure the branch `claude/quirky-ramanujan-pk7f77` is checked out and up to
   date, and that the one-time setup above is done (`python -m clipper doctor`
   reports no FAIL line).
2. Run `python -m clipper status`, then `python -m clipper plan`.
3. If `python -m clipper doctor` says the automatic review is on, run
   `python -m clipper review` and go to step 4. Otherwise do the review
   yourself: open the newest `clipper/out/<run>/plan.json`, and for each planned
   clip read its transcript (`text`) and look at its frames (the image at
   `sheet`), then:
   - **Gameplay only.** Set `"drop": true` if a streamer or commentator can be
     heard (someone reacting to or talking about the game from outside it,
     rather than characters in the game speaking) or seen (a webcam box, a chat
     overlay, a person on camera).
   - Also drop story cutscenes, endings and plot spoilers, gambling or political
     content, slurs, someone's personal information, and footage that is not
     GTA 6 gameplay.
   - **Section.** Set `"section"` to what happens in the clip: `findings` (a
     secret, easter egg or newly discovered detail), `tricks` (a tip or how-to),
     `glitches` (a bug or broken physics), otherwise `moments`.
   - **Title and hook** (`meta.title`, `meta.hook`). Write a plain title under 60
     characters that says what happens, from what the frames and transcript
     show. Do not invent details.
4. Run `python -m clipper execute`.
5. Commit `clipper/state` and `clipper/reports` and push to the branch.
6. Report: where the finished clips and their `.txt` files are, how many clips
   were made, their sections and titles, what was dropped and why, and anything
   in `clipper/reports/latest.md` under "Not fetched", "Left out: not
   gameplay-only" or "Failed clips".

Do not change `publish.mode`, the sources, the sections or the filters, and do
not try to get around a site's bot check. If YouTube refuses this computer as
well, say so and stop.
