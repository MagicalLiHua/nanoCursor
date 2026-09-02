"""NanoCursor evaluation adapter for the AgentEval issue sandbox."""

from nanocursor.eval.contract import ISSUE_AGENT_SYSTEM_PROMPT, TOOL_NAMES
from nanocursor.eval.tools import create_eval_registry

__all__ = ["ISSUE_AGENT_SYSTEM_PROMPT", "TOOL_NAMES", "create_eval_registry"]
