# Qwen3.8-27B-GGUF Inference

Local inference setup for [`unsloth/Qwen3.8-27B-GGUF`](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)
via [llama.cpp](https://github.com/ggml-org/llama.cpp).

## About the model

- **Base model:** `Qwen/Qwen3.8-27B` — a dense 27B-parameter, native vision-language
  model from the Qwen team (hybrid Gated DeltaNet / Gated Attention architecture,
  reported by the GGUF metadata as `qwen3_5`).
- **Context length:** 262,144 tokens natively, extensible to 1,000,000 via RoPE
  scaling (YaRN).
- **Thinking mode:** on by default, can be disabled per request; reasoning depth
  is tunable via `reasoning_effort`.
- **Vision:** understands images and video (STEM diagrams, documents, hour-scale
  video) via the bundled `mmproj` projector.
- **Quantization:** Unsloth Dynamic v3.0 GGUF quants, including imatrix and
  "UD" (Unsloth Dynamic) variants that keep sensitive layers at higher precision.
- **License:** Apache 2.0.

Since this architecture is very recent, you need a current build of llama.cpp —
`scripts/build_llama_cpp.sh` builds from the latest master rather than pinning
a release.

## Available quants

| File | Size (GB) | Notes |
|---|---|---|
| UD-IQ2_XXS | 8.4 | Smallest, biggest quality loss |
| UD-Q2_K_XL | 9.9 | |
| UD-IQ2_M | 9.6 | |
| UD-IQ3_XXS | 11.1 | |
| Q3_K_S | 11.7 | |
| Q3_K_M | 12.9 | |
| UD-Q3_K_XL | 12.5 | |
| IQ4_XS | 14.6 | |
| IQ4_NL | 15.2 | |
| Q4_0 | 15.0 | |
| Q4_K_S | 15.0 | |
| **Q4_K_M** | **15.9** | **Recommended default — good quality/size balance** |
| UD-Q4_K_XL | 16.7 | |
| Q4_1 | 16.3 | |
| Q5_K_S | 17.9 | |
| Q5_K_M | 18.5 | |
| UD-Q5_K_XL | 18.8 | |
| Q6_K | 21.3 | |
| UD-Q6_K_XL | 24.1 | |
| Q8_0 | 27.1 | Near-lossless |
| UD-Q8_K_XL | 29.3 | |

Plus `BF16/` (full precision, sharded) and `mmproj-F16.gguf` / `mmproj-BF16.gguf`
(vision projector, required for image/video input).

Rule of thumb: you need roughly (file size) + a few GB of RAM/VRAM headroom for
KV cache at the context length you run with — the 262K native context is large,
so start with a smaller `--ctx-size` (see below) unless you have a lot of memory.

## Setup

Run the server and the chat client in **separate terminals** — `run_server.sh`
runs in the foreground.

### CPU only, default quant (Q4_K_M)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/build_llama_cpp.sh
./scripts/download_model.sh
./scripts/run_server.sh
python scripts/chat.py
```

### NVIDIA GPU, explicit quant / context / port

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/build_llama_cpp.sh --cuda
./scripts/download_model.sh Q4_K_M
./scripts/run_server.sh Q4_K_M 32768 8080
python scripts/chat.py
```

### Script arguments

| Script | Arguments | Defaults |
|---|---|---|
| `build_llama_cpp.sh` | `--cuda` to enable an NVIDIA GPU build | CPU-only |
| `download_model.sh` | `QUANT` `DEST_DIR` | `Q4_K_M` `models` |
| `run_server.sh` | `QUANT` `CTX_SIZE` `PORT` | `Q4_K_M` `32768` `8080` |
| `chat.py` | `--no-thinking`, `--base-url`, `--model`, `--system`, `--max-tokens` | thinking mode, `http://localhost:8080/v1` |

`scripts/chat.py` applies the sampling parameters recommended by the model
card, which differ between thinking and instruct mode (temperature, top_p,
top_k, min_p, presence_penalty). Use `--no-thinking` for instruct mode.

You can also talk to the server with any OpenAI-compatible client pointed at
`http://localhost:8080/v1`, or use `llama.cpp/build/bin/llama-mtmd-cli` for a
one-shot multimodal (image/video) CLI session.
