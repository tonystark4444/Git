#!/usr/bin/env python3
"""Minimal chat client for a local Qwen3.8-27B-GGUF llama.cpp server.

Applies the sampling parameters recommended by the Qwen3.8 model card,
which differ between "thinking" mode (on by default) and "instruct" mode.

Usage:
    python scripts/chat.py                 # thinking mode, default server
    python scripts/chat.py --no-thinking   # instruct mode
    python scripts/chat.py --base-url http://localhost:8080/v1
"""
import argparse

from openai import OpenAI

THINKING_PARAMS = dict(temperature=1.0, top_p=0.95, presence_penalty=0.0)
INSTRUCT_PARAMS = dict(temperature=0.7, top_p=0.80, presence_penalty=1.5)
# top_k / min_p / repetition_penalty aren't part of the OpenAI Chat Completions
# schema; llama-server accepts them as extra body fields.
COMMON_EXTRA = dict(top_k=20, min_p=0.0, repeat_penalty=1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080/v1")
    parser.add_argument("--api-key", default="not-needed")
    parser.add_argument("--model", default="Qwen3.8-27B")
    parser.add_argument("--no-thinking", action="store_true", help="Disable thinking mode")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--system", default=None)
    args = parser.parse_args()

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    sampling = INSTRUCT_PARAMS if args.no_thinking else THINKING_PARAMS

    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})

    print(f"Connected to {args.base_url} | thinking={'off' if args.no_thinking else 'on'}")
    print("Type your message (Ctrl+C or /exit to quit).\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input == "/exit":
            break

        messages.append({"role": "user", "content": user_input})

        stream = client.chat.completions.create(
            model=args.model,
            messages=messages,
            max_tokens=args.max_tokens,
            stream=True,
            extra_body={
                **COMMON_EXTRA,
                "chat_template_kwargs": {"enable_thinking": not args.no_thinking},
            },
            **sampling,
        )

        print("assistant> ", end="", flush=True)
        reply = ""
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            reply += delta
            print(delta, end="", flush=True)
        print("\n")

        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
