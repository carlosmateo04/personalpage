#!/usr/bin/env bash
# The daily run: refresh yesterday's posts, make and schedule today's clips, save the ledger and report.
set -uo pipefail
cd "$(dirname "$0")"

./setup.sh || exit 1
python3 -m clipper status || echo "status refresh failed (continuing)"
python3 -m clipper run
code=$?

git add state reports
if ! git diff --cached --quiet; then
  git commit -q -m "Clipper daily run $(date -u +%Y-%m-%d)"
  branch="$(git branch --show-current)"
  for wait in 2 4 8 16; do
    git push -q -u origin "$branch" && break
    sleep "$wait"
  done
fi
exit $code
