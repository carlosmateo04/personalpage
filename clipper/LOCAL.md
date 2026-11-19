# Running the clipper on your own computer

YouTube refuses video downloads from cloud machines, so the daily run has to
happen on a home internet connection. Everything else is the same.

It has been run on Linux only. macOS should behave the same; Windows is untested.

## What the computer needs

- Python 3.11 or newer
- ffmpeg (macOS: `brew install ffmpeg`; Windows: `winget install ffmpeg`; Linux: `apt-get install -y ffmpeg`)
- git, and about 2 GB of free disk space

## One-time setup

```bash
git clone https://github.com/carlosmateo04/personalpage.git
```

```bash
cd personalpage && git checkout claude/quirky-ramanujan-pk7f77 && cd clipper
```

```bash
python3 -m venv .venv
```

Activate it (macOS and Linux):

```bash
source .venv/bin/activate
```

Activate it (Windows):

```bash
.venv\Scripts\activate
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

Review `out/<newest run>/plan.json` (see below), then:

```bash
python -m clipper execute
```

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
3. Open the newest `clipper/out/<run>/plan.json`. For each planned clip read its
   transcript (`text`) and look at its frame strip (the image at `sheet`), then:
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
