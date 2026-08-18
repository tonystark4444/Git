#!/usr/bin/env bash
# Build llama.cpp from source.
#
# Qwen3.8 uses a brand-new hybrid Gated DeltaNet / Gated Attention architecture
# (reported by the GGUF as "qwen3_5"). Support for it landed recently, so a
# release build of llama.cpp may be too old — build from the latest master.
#
# Usage:
#   ./scripts/build_llama_cpp.sh [--cuda]
set -euo pipefail

CUDA=0
if [[ "${1:-}" == "--cuda" ]]; then
  CUDA=1
fi

if [[ ! -d llama.cpp ]]; then
  git clone https://github.com/ggml-org/llama.cpp.git
fi

cd llama.cpp
git pull --ff-only

CMAKE_ARGS=(-B build -DCMAKE_BUILD_TYPE=Release)
if [[ "$CUDA" == "1" ]]; then
  CMAKE_ARGS+=(-DGGML_CUDA=ON)
fi

cmake "${CMAKE_ARGS[@]}"
cmake --build build --config Release -j"$(nproc)" --target llama-server llama-cli llama-mtmd-cli

echo "Built binaries in llama.cpp/build/bin/"
