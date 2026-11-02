#!/usr/bin/env bash
# Reassemble split IPA parts (<name>.ipa.partNNN) in order, check for gaps, verify SHA-256.
# Usage: scripts/00_reassemble.sh <upload_dir> [name]
set -euo pipefail
DIR="$1"; NAME="${2:-}"
cd "$(dirname "$0")/.."
if [[ -z "$NAME" ]]; then
  NAME=$(ls "$DIR" | grep -oE '[^/]*\.ipa\.part[0-9]+$' | sed -E 's/^[0-9a-f]{8}-//; s/\.part[0-9]+$//' | sort -u | head -1)
fi
mapfile -t PARTS < <(ls "$DIR" | grep -E "${NAME//./\\.}\.part[0-9]+$" | sort -t. -k1,1 | awk -F'.part' '{print $NF"\t"$0}' | sort -n | cut -f2)
N=${#PARTS[@]}; echo "Found $N parts for $NAME"
i=1
for p in "${PARTS[@]}"; do
  num=$(echo "$p" | sed -E 's/.*\.part0*([0-9]+)$/\1/')
  [[ "$num" == "$i" ]] || { echo "GAP: expected part $i, got $p" >&2; exit 1; }
  echo "  part $(printf %03d $i): $(stat -c %s "$DIR/$p") bytes  $p"; i=$((i+1))
done
OUT="input/$NAME"
: > "$OUT.tmp"; for p in "${PARTS[@]}"; do cat "$DIR/$p" >> "$OUT.tmp"; done
mv "$OUT.tmp" "$OUT"
EXPECTED=$(cat "$DIR"/*"$NAME.sha256" | awk '{print $1}')
ACTUAL=$(sha256sum "$OUT" | awk '{print $1}')
echo "expected $EXPECTED"; echo "actual   $ACTUAL"
[[ "$EXPECTED" == "$ACTUAL" ]] && echo "SHA-256 OK -> $OUT ($(stat -c %s "$OUT") bytes)" || { echo "SHA-256 MISMATCH" >&2; rm -f "$OUT"; exit 1; }
chmod a-w "$OUT"
