#!/usr/bin/env bash
# StreamBridge — one-time developer setup for macOS.
# Safe to re-run; every step checks before acting.
set -euo pipefail

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

bold "StreamBridge setup"
echo

# ---------------------------------------------------------------- platform ---
if [[ "$(uname -s)" != "Darwin" ]]; then
  warn "Not macOS — the frontend will build, but 'npm run app:build' cannot"
  warn "produce a .dmg anywhere except macOS."
fi

# ------------------------------------------------------------ xcode clt -----
bold "Xcode command-line tools"
if xcode-select -p >/dev/null 2>&1; then
  ok "installed at $(xcode-select -p)"
else
  warn "not installed — launching the installer"
  xcode-select --install || true
  die "Re-run ./setup.sh once the Xcode command-line tools finish installing."
fi

# ------------------------------------------------------------------ node ----
bold "Node.js"
if command -v node >/dev/null 2>&1; then
  node_major="$(node --version | sed 's/^v\([0-9]*\).*/\1/')"
  if (( node_major < 20 )); then
    die "Node $(node --version) is too old. Install Node 20+ (brew install node)."
  fi
  ok "$(node --version)  (npm $(npm --version))"
else
  die "Node.js not found. Install Node 20+ — 'brew install node' or nodejs.org."
fi

# ------------------------------------------------------------------ rust ----
bold "Rust"
if command -v cargo >/dev/null 2>&1; then
  ok "$(rustc --version)"
else
  warn "not found — installing via rustup"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
  # shellcheck disable=SC1090
  source "$HOME/.cargo/env"
  ok "$(rustc --version)"
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  bold "Rust targets (for universal binaries)"
  for target in aarch64-apple-darwin x86_64-apple-darwin; do
    if rustup target list --installed 2>/dev/null | grep -qx "$target"; then
      ok "$target"
    else
      rustup target add "$target" && ok "$target (added)"
    fi
  done
fi

# ---------------------------------------------------------------- ffmpeg ----
bold "ffmpeg"
# StreamBridge shells out to ffmpeg and ffprobe rather than bundling them.
# Using the copy already on the machine keeps the app clear of ffmpeg's GPL
# distribution obligations, and Homebrew's build is better maintained than
# anything this script could fetch.
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ok "$(ffmpeg -version | head -1 | cut -d' ' -f1-3)  ($(command -v ffmpeg))"
elif command -v brew >/dev/null 2>&1; then
  warn "not found - installing with Homebrew"
  brew install ffmpeg
  ok "$(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
else
  warn "ffmpeg is missing and Homebrew is not installed."
  cat <<'EOF'

  StreamBridge cannot stream without ffmpeg. Install Homebrew first:

    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  then re-run ./setup.sh.

  On Apple Silicon, Homebrew installs to /opt/homebrew - if your ~/.zprofile
  points at /usr/local/bin/brew you will see "no such file" on every new shell.
  The installer prints the correct line to add.

EOF
  die "Install ffmpeg, then re-run ./setup.sh."
fi

# ---------------------------------------------------------------- deps ------
bold "JavaScript dependencies"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
ok "node_modules ready"

# ---------------------------------------------------------------- done ------
echo
bold "Setup complete"
cat <<'EOF'

  Next steps:

    npm run app:dev             live-reload development window
    npm run app:build           build a .dmg for this Mac
    npm run app:build:universal build a .dmg for Intel + Apple Silicon

  The .dmg lands in:

    src-tauri/target/release/bundle/dmg/                      (app:build)
    src-tauri/target/universal-apple-darwin/release/bundle/dmg/  (universal)

EOF
