#!/usr/bin/env bash
# Phase 1: unpack input/*.ipa into work/unpacked (pristine copy, never modified afterwards).
set -euo pipefail
cd "$(dirname "$0")/.."
IPA=$(ls input/*.ipa | head -1)
rm -rf work/unpacked && mkdir -p work/unpacked
unzip -q "$IPA" -d work/unpacked
echo "Unpacked $IPA -> work/unpacked"
