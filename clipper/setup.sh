#!/usr/bin/env bash
# Install what the pipeline needs. Safe to run again: finished steps are skipped.
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "ffmpeg is required (apt-get install -y ffmpeg)"; exit 1; }

python3 -c "import numpy, soundfile, sherpa_onnx, upload_post, requests, yt_dlp" 2>/dev/null \
  || python3 -m pip install -q --root-user-action=ignore -r requirements.txt

MODELS="${CLIPPER_MODELS:-$HOME/.cache/clipper/models}"
BASE="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
ASR="sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
mkdir -p "$MODELS"

# Download with resume: a dropped connection continues where it stopped.
fetch() {
  local url="$1" out="$2" try
  for try in 1 2 3 4 5 6 7 8; do
    curl -fsSL -C - -o "$out.part" "$url" && mv "$out.part" "$out" && return 0
    echo "download interrupted (attempt $try), resuming"
    sleep $((try * 2))
  done
  echo "could not download $url"
  return 1
}

if [ ! -f "$MODELS/$ASR/encoder.int8.onnx" ]; then
  echo "downloading the speech model (about 480 MB)"
  fetch "$BASE/$ASR.tar.bz2" "$MODELS/asr.tar.bz2"
  tar -xjf "$MODELS/asr.tar.bz2" -C "$MODELS"
  rm -f "$MODELS/asr.tar.bz2"
fi
if [ ! -f "$MODELS/silero_vad.onnx" ]; then
  fetch "$BASE/silero_vad.onnx" "$MODELS/silero_vad.onnx"
fi
echo "setup complete"
