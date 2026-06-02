#!/usr/bin/env python3
"""Run nanoCursor aggregate agent-runtime eval suites."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="core", choices=["core"], help="Eval suite to run")
    parser.add_argument("--workspace-dir", default="", help="Workspace used to store eval artifacts")
    parser.add_argument("--task-eval", action="append", help="Restrict task-scoring eval ids")
    parser.add_argument("--no-persist", action="store_true", help="Do not write eval result artifacts")
    parser.add_argument("--json", action="store_true", help="Print raw JSON only")
    parser.add_argument("--summary", action="store_true", help="Show persisted aggregate eval trend summary")
    parser.add_argument("--history", action="store_true", help="List persisted aggregate eval runs")
    parser.add_argument("--limit", type=int, default=20, help="History/summary run limit")
    return parser.parse_args(argv)


def _print_human(result: dict[str, Any]) -> None:
    print("nanoCursor Agent Evals")
    print()
    print(f"Suite: {result.get('suite')}")
    print(f"Status: {result.get('status')}")
    print(f"Checks: {result.get('passed')}/{result.get('total')} passed")
    print(f"Pass rate: {result.get('pass_rate')}")
    if result.get("eval_run_id"):
        print(f"Eval run: {result.get('eval_run_id')}")
    print()
    for section in result.get("sections", []):
        print(
            f"- {section.get('label') or section.get('id')}: "
            f"{section.get('status')} "
            f"({section.get('passed')}/{section.get('total')})"
        )
        failed_cases = section.get("failed_cases") or []
        if failed_cases:
            print(f"  failed cases: {', '.join(map(str, failed_cases))}")
        failed_results = [
            item.get("id")
            for item in section.get("results", []) or section.get("cases", [])
            if item.get("status") != "passed"
        ]
        if failed_results:
            print(f"  failed results: {', '.join(map(str, failed_results))}")


def _route_stdout_loggers_to_stderr() -> None:
    """Keep --json stdout machine-readable even if project loggers emit info."""
    for logger_name in ("nanoCursor",):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers:
            if getattr(handler, "stream", None) is sys.stdout:
                handler.stream = sys.stderr


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    from src.api.services.agent_eval_service import (
        list_agent_eval_runs,
        run_agent_eval_suite,
        summarize_agent_eval_runs,
    )

    if args.json:
        _route_stdout_loggers_to_stderr()
        with contextlib.redirect_stdout(sys.stderr):
            if args.summary:
                result = summarize_agent_eval_runs(args.workspace_dir or None, limit=args.limit)
            elif args.history:
                result = list_agent_eval_runs(args.workspace_dir or None, limit=args.limit)
            else:
                result = run_agent_eval_suite(
                    args.suite,
                    workspace_dir=args.workspace_dir or None,
                    persist=not args.no_persist,
                    task_eval_ids=args.task_eval,
                )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.summary:
            result = summarize_agent_eval_runs(args.workspace_dir or None, limit=args.limit)
            print("nanoCursor Agent Eval Summary")
            print()
            print(f"Runs: {result.get('passed_runs')}/{result.get('total_runs')} passed")
            print(f"Run pass rate: {result.get('run_pass_rate')}")
            print(f"Average check pass rate: {result.get('avg_check_pass_rate')}")
        elif args.history:
            result = list_agent_eval_runs(args.workspace_dir or None, limit=args.limit)
            print("nanoCursor Agent Eval History")
            print()
            for run in result.get("runs", []):
                print(f"- {run.get('eval_run_id')}: {run.get('status')} ({run.get('passed')}/{run.get('total')})")
        else:
            result = run_agent_eval_suite(
                args.suite,
                workspace_dir=args.workspace_dir or None,
                persist=not args.no_persist,
                task_eval_ids=args.task_eval,
            )
            _print_human(result)
    if args.summary or args.history:
        return 0
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
