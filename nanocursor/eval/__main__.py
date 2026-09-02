from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from nanocursor.client import create_client
from nanocursor.config import ProviderConfig, load_config
from nanocursor.eval.bridge_client import BridgeClient
from nanocursor.eval.contract import ISSUE_AGENT_SYSTEM_PROMPT, TOOL_NAMES
from nanocursor.eval.runner import registry_contract, run_evaluation


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run NanoCursor against the AgentEval issue sandbox.")
    result.add_argument("--prompt", help="Issue prompt. Prefer --prompt-file for long prompts.")
    result.add_argument("--prompt-file", type=Path)
    result.add_argument("--task-id", default="unknown-task")
    result.add_argument("--run-id", default=f"nanocursor-{uuid4().hex[:8]}")
    result.add_argument("--config", type=Path)
    result.add_argument("--base-url")
    result.add_argument("--model")
    result.add_argument("--context-window", type=int, default=1_000_000)
    result.add_argument("--max-output-tokens", type=int, default=32_768)
    result.add_argument("--output-dir", type=Path, default=Path(".artifacts/nanocursor-eval"))
    result.add_argument("--bridge-url", default=os.environ.get("NANOCURSOR_TOOL_BRIDGE_URL", ""))
    result.add_argument("--bridge-token", default=os.environ.get("NANOCURSOR_TOOL_BRIDGE_TOKEN", ""))
    result.add_argument("--max-turns", type=int, default=96)
    result.add_argument("--max-wall-seconds", type=int, default=1_200)
    result.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the evaluation contract without loading model credentials or calling an API.",
    )
    return result


async def run(args: argparse.Namespace) -> int:
    if args.max_turns < 1:
        raise ValueError("--max-turns must be positive.")
    if args.max_wall_seconds < 1:
        raise ValueError("--max-wall-seconds must be positive.")

    if args.validate_only:
        transport = None
        async with BridgeClient("http://127.0.0.1:1", "validation-token", transport=transport) as bridge:
            schemas = registry_contract(bridge)
        print(
            json.dumps(
                {
                    "protocolVersion": "1",
                    "tools": [schema["name"] for schema in schemas],
                    "expectedTools": list(TOOL_NAMES),
                    "systemPromptSha256": hashlib.sha256(ISSUE_AGENT_SYSTEM_PROMPT.encode()).hexdigest(),
                    "modelApiCalled": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.prompt
    if not prompt:
        raise ValueError("Provide --prompt or --prompt-file.")
    if not args.bridge_url or not args.bridge_token:
        raise ValueError("Tool bridge URL and token are required.")

    if args.config:
        provider = load_config(args.config).providers[0]
    else:
        if not args.base_url or not args.model:
            raise ValueError("Provide --config, or provide both --base-url and --model.")
        provider = ProviderConfig(
            name="agent-eval",
            protocol="openai-compat",
            base_url=args.base_url,
            model=args.model,
            context_window=args.context_window,
            max_output_tokens=args.max_output_tokens,
        )
    async with BridgeClient(args.bridge_url, args.bridge_token) as bridge:
        await bridge.health()
        summary = await run_evaluation(
            client=create_client(provider),
            protocol=provider.protocol,
            model=provider.model,
            prompt=prompt,
            task_id=args.task_id,
            run_id=args.run_id,
            bridge_client=bridge,
            output_dir=args.output_dir,
            max_turns=args.max_turns,
            max_wall_time_seconds=args.max_wall_seconds,
            context_window=provider.get_context_window(),
        )
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0 if summary.status == "completed" else 2


def main() -> None:
    args = parser().parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"NanoCursor evaluation error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
