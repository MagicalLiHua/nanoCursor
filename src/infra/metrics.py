"""
可观测性指标模块 (Observability Metrics)
记录 LLM 调用的 token 消耗、延迟、修复轮数、工具成功率等指标。
"""

import json
import logging
import time
from threading import Lock

logger = logging.getLogger(__name__)


class MetricsCollector:
    """线程安全的指标收集器，按维度聚合数据并输出到日志和文件。"""

    def __init__(self, output_file: str = None):
        self._lock = Lock()
        # LLM 调用指标
        self.llm_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_llm_tokens = 0
        self.total_llm_latency_ms = 0
        self.llm_latency_records: list[float] = []
        # 工具调用指标
        self.tool_calls = 0
        self.tool_successes = 0
        self.tool_failures = 0
        self.tool_failure_reasons: list[str] = []
        # 修复循环指标
        self.repair_cycles = 0
        self.repair_cycle_outcomes: list[dict] = []
        # Supervisor 决策指标
        self.recent_supervisor_decisions: list[dict] = []
        # 原始记录（最近 N 条）
        self.recent_llm_records: list[dict] = []
        self.recent_tool_records: list[dict] = []
        self.output_file = output_file

    # ----- LLM 指标 -----

    def record_llm_call_start(self) -> float:
        """记录一次 LLM 调用的开始，返回时间戳。"""
        return time.perf_counter()

    def record_llm_call_end(self, start_time: float, input_tokens: int = 0, output_tokens: int = 0, node_name: str = "unknown"):
        """记录一次 LLM 调用的完成，自动计算延迟并归档。"""
        latency_ms = (time.perf_counter() - start_time) * 1000
        with self._lock:
            self.llm_calls += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_llm_tokens += input_tokens + output_tokens
            self.total_llm_latency_ms += latency_ms
            self.llm_latency_records.append(latency_ms)
            self.recent_llm_records.append({
                "node": node_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": round(latency_ms, 1),
            })
            if len(self.recent_llm_records) > 50:
                self.recent_llm_records = self.recent_llm_records[-50:]
        logger.info(f"[Metrics] LLM 调用 | node={node_name} | in={input_tokens} out={output_tokens} | latency={latency_ms:.0f}ms")

    # ----- 工具调用指标 -----

    def record_tool_success(self, tool_name: str):
        with self._lock:
            self.tool_calls += 1
            self.tool_successes += 1

    def record_tool_failure(self, tool_name: str, reason: str):
        with self._lock:
            self.tool_calls += 1
            self.tool_failures += 1
            self.tool_failure_reasons.append(f"{tool_name}: {reason[:200]}")
            self.recent_tool_records.append({
                "tool": tool_name,
                "status": "failure",
                "reason": reason[:200],
            })
            if len(self.recent_tool_records) > 20:
                self.recent_tool_records = self.recent_tool_records[-20:]
        logger.warning(f"[Metrics] 工具调用失败 | tool={tool_name} | reason={reason[:100]}")

    # ----- 修复循环指标 -----

    def record_repair_cycle_start(self):
        with self._lock:
            self.repair_cycles += 1

    def record_repair_cycle_outcome(self, outcome: str, error_summary: str = ""):
        """outcome: 'fixed' | 'still_failing'"""
        with self._lock:
            self.repair_cycle_outcomes.append({
                "outcome": outcome,
                "error": error_summary[:300],
            })

    # ----- Supervisor 决策指标 -----

    def record_supervisor_decision(self, decision: str, task_id: str | None, reasoning: str):
        """Record a Supervisor routing decision."""
        with self._lock:
            self.recent_supervisor_decisions.append({
                "decision": decision,
                "task_id": task_id,
                "reasoning": reasoning[:200],
            })
            if len(self.recent_supervisor_decisions) > 50:
                self.recent_supervisor_decisions = self.recent_supervisor_decisions[-50:]

    # ----- 输出与上报 -----

    def dump_summary(self) -> dict:
        """导出当前指标的完整快照。"""
        with self._lock:
            avg_tokens = (
                self.total_llm_tokens / self.llm_calls if self.llm_calls > 0 else 0
            )
            avg_latency = (
                self.total_llm_latency_ms / self.llm_calls if self.llm_calls > 0 else 0
            )
            tool_success_rate = (
                self.tool_successes / self.tool_calls if self.tool_calls > 0 else 0
            )
            return {
                "llm": {
                    "total_calls": self.llm_calls,
                    "total_tokens": self.total_llm_tokens,
                    "total_input_tokens": self.total_input_tokens,
                    "total_output_tokens": self.total_output_tokens,
                    "avg_tokens_per_call": round(avg_tokens, 1),
                    "avg_latency_ms": round(avg_latency, 1),
                    "max_latency_ms": round(max(self.llm_latency_records), 1)
                    if self.llm_latency_records
                    else 0,
                    "min_latency_ms": round(min(self.llm_latency_records), 1)
                    if self.llm_latency_records
                    else 0,
                },
                "tool_calls": {
                    "total": self.tool_calls,
                    "successes": self.tool_successes,
                    "failures": self.tool_failures,
                    "success_rate": round(tool_success_rate, 2),
                    "failure_reasons": self.tool_failure_reasons[-10:],
                },
                "repair_cycles": {
                    "total": self.repair_cycles,
                    "outcomes": self.repair_cycle_outcomes,
                },
                "supervisor_decisions": self.recent_supervisor_decisions[-10:],
                "recent_llm_records": self.recent_llm_records[-10:],
            }

    def flush_to_file(self):
        """将指标快照写入当前文件。"""
        if not self.output_file:
            return
        summary = self.dump_summary()
        try:
            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            logger.info(f"[Metrics] 指标已写入 {self.output_file}")
        except Exception as e:
            logger.error(f"[Metrics] 写入指标文件失败: {e}")

    def append_to_history(self, history_file: str, tag: str = ""):
        """追加一条带时间戳的指标快照到历史文件（用于长期持久化）。"""
        summary = self.dump_summary()
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tag": tag,
            "llm_calls": summary["llm"]["total_calls"],
            "total_tokens": summary["llm"]["total_tokens"],
            "avg_latency_ms": summary["llm"]["avg_latency_ms"],
            "tool_success_rate": summary["tool_calls"]["success_rate"],
            "tool_calls": summary["tool_calls"]["total"],
            "repair_cycles": summary["repair_cycles"]["total"],
        }
        try:
            existing = []
            if _os.path.exists(history_file):
                with open(history_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing.append(entry)
            # Keep last 500 entries
            if len(existing) > 500:
                existing = existing[-500:]
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Metrics] 写入历史文件失败: {e}")

    def render_summary(self) -> str:
        """渲染指标的完整摘要为可读字符串，适合 CLI 打印或 UI 展示。"""
        summary = self.dump_summary()
        lines: list[str] = []
        lines.append("=" * 50)
        lines.append(" 指标摘要")
        lines.append("=" * 50)
        llm = summary["llm"]
        tools_data = summary["tool_calls"]
        repair = summary["repair_cycles"]
        lines.append(f"  LLM 调用次数: {llm['total_calls']}")
        lines.append(f"  ↑ 输入 Token: {llm.get('total_input_tokens', 0)}")
        lines.append(f"  ↓ 输出 Token: {llm.get('total_output_tokens', 0)}")
        lines.append(f"  ∑ 总 Token: {llm['total_tokens']}")
        lines.append(f"  平均延迟: {llm['avg_latency_ms']:.0f}ms")
        if llm['max_latency_ms'] > 0:
            lines.append(f"  最大延迟: {llm['max_latency_ms']:.0f}ms")
        lines.append(f"  工具调用成功率: {tools_data['success_rate']:.0%} ({tools_data['successes']}/{tools_data['total']})")
        if tools_data['failure_reasons']:
            lines.append(f"  最近失败原因 ({len(tools_data['failure_reasons'])} 次):")
            for reason in tools_data['failure_reasons'][-5:]:
                lines.append(f"    - {reason}")
        lines.append(f"  修复循环次数: {repair['total']}")
        for outcome in repair['outcomes']:
            lines.append(f"    - {outcome['outcome']}: {outcome.get('error', '')[:80]}")
        lines.append("=" * 50)
        self.flush_to_file()
        return "\n".join(lines)


# 全局单例（使用绝对路径，避免 CWD 变化导致写入位置错误）
import os as _os

from src.infra.config import WORKSPACE_DIR as _WORKSPACE_DIR

metrics = MetricsCollector(output_file=_os.path.join(_WORKSPACE_DIR, "metrics.json"))
