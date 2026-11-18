#!/usr/bin/env bash
# Make a channel avatar (800x800) and banner (2560x1440) from a name and a tagline.
# Usage: ./brand.sh "Leonida Clips" "UNOFFICIAL FAN CHANNEL"
set -euo pipefail
cd "$(dirname "$0")"
NAME="${1:-Leonida Clips}"
TAG="${2:-UNOFFICIAL FAN CHANNEL}"
FONT="assets/fonts/Montserrat-ExtraBold.ttf"
OUT="assets/brand"
mkdir -p "$OUT"
INITIALS="$(echo "$NAME" | awk '{for(i=1;i<=NF&&i<=2;i++) printf toupper(substr($i,1,1))}')"
UPPER="$(echo "$NAME" | tr '[:lower:]' '[:upper:]')"
bg() { echo "gradients=s=${1}x${2}:c0=0xFF2E93:c1=0x7B2FF7:x0=0:y0=0:x1=${1}:y1=${2}"; }

ffmpeg -y -loglevel error -f lavfi -i "$(bg 800 800)" -frames:v 1 -vf "\
drawtext=fontfile=$FONT:text='$INITIALS':fontsize=360:fontcolor=white:borderw=14:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-30,\
drawtext=fontfile=$FONT:text='FAN CLIPS':fontsize=64:fontcolor=0xFFE600:borderw=6:bordercolor=black:x=(w-text_w)/2:y=h-170" \
  "$OUT/avatar.png"

ffmpeg -y -loglevel error -f lavfi -i "$(bg 2560 1440)" -frames:v 1 -vf "\
drawtext=fontfile=$FONT:text='$UPPER':fontsize=150:fontcolor=white:borderw=10:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2-60,\
drawtext=fontfile=$FONT:text='$TAG':fontsize=58:fontcolor=0xFFE600:borderw=6:bordercolor=black:x=(w-text_w)/2:y=(h/2)+70" \
  "$OUT/banner.png"
echo "$OUT/avatar.png"
echo "$OUT/banner.png"
