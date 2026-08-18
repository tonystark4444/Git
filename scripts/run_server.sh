#!/usr/bin/env bash
# Serve Qwen3.8-27B-GGUF through llama.cpp's OpenAI-compatible server.
#
# Usage:
#   ./scripts/run_server.sh [QUANT] [CTX_SIZE] [PORT]
#
# QUANT defaults to Q4_K_M, CTX_SIZE defaults to 32768 (raise up to 262144 for
# the model's native max, memory permitting), PORT defaults to 8080.
set -euo pipefail

QUANT="${1:-Q4_K_M}"
CTX_SIZE="${2:-32768}"
PORT="${3:-8080}"
DEST_DIR="models"

MODEL_PATH="${DEST_DIR}/Qwen3.8-27B-${QUANT}.gguf"
MMPROJ_PATH="${DEST_DIR}/mmproj-F16.gguf"
SERVER_BIN="llama.cpp/build/bin/llama-server"

[[ -x "$SERVER_BIN" ]] || { echo "llama-server not built. Run scripts/build_llama_cpp.sh first." >&2; exit 1; }
[[ -f "$MODEL_PATH" ]] || { echo "Model not found at $MODEL_PATH. Run scripts/download_model.sh $QUANT first." >&2; exit 1; }

ARGS=(
  -m "$MODEL_PATH"
  --ctx-size "$CTX_SIZE"
  --port "$PORT"
  --host 0.0.0.0
  --jinja
  --flash-attn on
)

if [[ -f "$MMPROJ_PATH" ]]; then
  ARGS+=(--mmproj "$MMPROJ_PATH")
else
  echo "Note: mmproj not found at $MMPROJ_PATH — starting in text-only mode." >&2
fi

echo "Starting llama-server on :${PORT} with ${MODEL_PATH} (ctx=${CTX_SIZE})"
exec "$SERVER_BIN" "${ARGS[@]}"
