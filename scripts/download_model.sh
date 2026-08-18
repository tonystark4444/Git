#!/usr/bin/env bash
# Download a GGUF quant of unsloth/Qwen3.8-27B-GGUF (+ vision mmproj) from Hugging Face.
#
# Usage:
#   ./scripts/download_model.sh [QUANT] [DEST_DIR]
#
# QUANT defaults to Q4_K_M. See README.md for the full quant table.
# Vision (image/video) support requires the mmproj file, which is always downloaded.
set -euo pipefail

REPO="unsloth/Qwen3.8-27B-GGUF"
QUANT="${1:-Q4_K_M}"
DEST_DIR="${2:-models}"
MODEL_FILE="Qwen3.8-27B-${QUANT}.gguf"
MMPROJ_FILE="mmproj-F16.gguf"

command -v huggingface-cli >/dev/null 2>&1 || {
  echo "huggingface-cli not found. Install with: pip install -r requirements.txt" >&2
  exit 1
}

mkdir -p "$DEST_DIR"

echo "Downloading ${MODEL_FILE} from ${REPO} into ${DEST_DIR}/ ..."
huggingface-cli download "$REPO" "$MODEL_FILE" --local-dir "$DEST_DIR"

echo "Downloading vision projector ${MMPROJ_FILE} ..."
huggingface-cli download "$REPO" "$MMPROJ_FILE" --local-dir "$DEST_DIR"

echo "Done. Model: ${DEST_DIR}/${MODEL_FILE}"
echo "      mmproj: ${DEST_DIR}/${MMPROJ_FILE}"
