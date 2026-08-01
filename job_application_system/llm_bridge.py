"""Subprocess bridge for simple LLM chat completions via Track A client."""
import argparse
import json
import sys
from pathlib import Path

# Allow job_application_system relative imports.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "job_application_system"))

from utils.llm_client import llm_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a single LLM chat completion.")
    parser.add_argument("--prompt", required=True, help="Path to JSON file with prompt, temperature, max_tokens")
    args = parser.parse_args()

    with open(args.prompt, "r", encoding="utf-8") as f:
        config = json.load(f)

    prompt = config["prompt"]
    temperature = config.get("temperature", 0.2)
    max_tokens = config.get("max_tokens", 256)

    messages = [
        {"role": "system", "content": "You are a helpful assistant. Return concise answers."},
        {"role": "user", "content": prompt},
    ]

    try:
        content = llm_client.chat(messages, temperature=temperature, max_tokens=max_tokens)
        print(json.dumps({"ok": True, "content": content}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
