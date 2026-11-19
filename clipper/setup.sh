#!/usr/bin/env bash
# Install what the pipeline needs. Safe to run again: finished steps are skipped.
# The work is done by `python -m clipper setup`, which also runs on Windows.
set -euo pipefail
cd "$(dirname "$0")"
PIP_ROOT_USER_ACTION=ignore python3 -m clipper setup --install
