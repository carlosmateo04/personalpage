# Backups

## You already have more than you think

Git is distributed. **Every clone is a complete backup** — all files, all
history, every branch. Not a snapshot of the latest state: the whole repository.

So the question is not "is there a backup" but "how many machines hold one, and
is any of them yours". Right now the answer may be *none*, because this project
has been built entirely in the cloud. That is the actual gap.

## What is worth backing up, in order

| | Replaceable? | Where it should live |
| --- | --- | --- |
| **The updater signing key** | **No. Ever.** | Password manager, nowhere else |
| **The last working `.dmg`** | Rebuildable, but slowly | Any cloud drive |
| **The source and its history** | Only from another clone | A second git remote |
| **Your accounts and stream keys** | Re-pasteable from the platforms | Already on your Mac — Time Machine covers it |

The signing key is the only thing on this list that cannot be reconstructed
from anything else. If it is lost, installed copies refuse every future update
and everyone reinstalls a `.dmg` by hand, permanently.

The `.dmg` is second for a reason that has nothing to do with code: if
everything else burned down tomorrow, a copy of the last working build is what
keeps the streams running while it is sorted out.

## 1 · Clone it to your Mac

The single most valuable thing, and it takes one command.

```bash
git clone https://github.com/carlosmateo04/personalpage.git ~/caudal
```

That is now a complete, independent copy of everything. If GitHub disappeared
this afternoon, you could keep working from it and push it somewhere else
later.

## 2 · Mirror it into a drive you already sync

A *bare* repository is a git repo with no working files — just the history. It
sits happily inside iCloud Drive, Google Drive, or Dropbox and syncs like any
other folder.

```bash
# once — create the mirror wherever your cloud folder is
git clone --bare ~/caudal ~/"Google Drive/backups/caudal.git"

# once — teach your working copy about it
cd ~/caudal
git remote add backup ~/"Google Drive/backups/caudal.git"

# whenever you want a backup
git push backup --all && git push backup --tags
```

To restore from it, with no GitHub involved at all:

```bash
git clone ~/"Google Drive/backups/caudal.git" ~/caudal-restored
```

*Verified end to end: mirrored, then restored from the mirror alone, and the
restored copy had all 17 commits and every file.*

## 3 · Keep the last `.dmg`

Drop the installer next to the mirror. It is the difference between "we lost
the tooling" and "we lost the streams".

## Rebuilding without GitHub

If Actions were unavailable, the `.dmg` is built locally with:

```bash
cd ~/caudal
npm ci
npm run app:build:universal
```

Both lockfiles — `package-lock.json` and `Cargo.lock` — are committed, so this
resolves to the same dependency versions CI used rather than to whatever is
newest today.

**Nothing here ever requires reverse engineering the app.** The `.dmg` is built
from this source; the source is the original, not a derivative of the binary. A
lost signing key costs you in-app updates, not the ability to build.

## About Obsidian

Right instinct, wrong tool for the code. Obsidian syncs a folder of markdown;
a repository is thousands of files, a binary `.git` directory, and a
`node_modules` tree that must not be synced. Pointing a notes vault at it tends
to produce sync conflicts inside `.git`, which is the one place a conflict does
real damage.

Where it does fit: `ROADMAP.md`, `REGRESSION.md`, `UPDATES.md`, and this file
are plain markdown and read perfectly in a vault. Copying those in gives you
the decisions, the test checklists, and the setup steps on every device you
own — which is genuinely useful, and none of it is code.
