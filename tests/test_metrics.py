"""
Tests for src/core/metrics.py

Covers:
- MetricsCollector record methods
- dump_summary output structure
- flush_to_file
"""

import json
import os
import tempfile

import pytest

from src.core.metrics import MetricsCollector


class TestMetricsCollector:
    """Unit tests for MetricsCollector (thread-safe, in-process)."""

    def test_records_llm_call(self):
        """record_llm_call_end increments counters."""
        mc = MetricsCollector()
        start = mc.record_llm_call_start()
        mc.record_llm_call_end(start, tokens_used=1000, node_name="planner")

        summary = mc.dump_summary()
        assert summary["llm"]["total_calls"] == 1
        assert summary["llm"]["total_tokens"] == 1000

    def test_records_multiple_llm_calls(self):
        """Multiple calls accumulate correctly."""
        mc = MetricsCollector()
        for i in range(3):
            start = mc.record_llm_call_start()
            mc.record_llm_call_end(start, tokens_used=100 + i, node_name="test")

        summary = mc.dump_summary()
        assert summary["llm"]["total_calls"] == 3
        assert summary["llm"]["total_tokens"] == 303

    def test_llm_latency_recorded(self):
        """Latency is recorded and min/max/avg are computed."""
        mc = MetricsCollector()

        # Record calls with known latencies (simulate via direct append for determinism)
        mc.llm_latency_records = [100.0, 200.0, 300.0]
        mc.llm_calls = 3
        mc.total_llm_latency_ms = 600.0

        summary = mc.dump_summary()
        assert summary["llm"]["max_latency_ms"] == 300.0
        assert summary["llm"]["min_latency_ms"] == 100.0
        assert summary["llm"]["avg_latency_ms"] == 200.0

    def test_empty_metrics_returns_zeros(self):
        """Empty collector returns zeroed summary without crashing."""
        mc = MetricsCollector()
        summary = mc.dump_summary()

        assert summary["llm"]["total_calls"] == 0
        assert summary["llm"]["total_tokens"] == 0
        assert summary["llm"]["max_latency_ms"] == 0
        assert summary["llm"]["min_latency_ms"] == 0
        assert summary["tool_calls"]["total"] == 0
        assert summary["repair_cycles"]["total"] == 0

    def test_tool_success_recorded(self):
        """record_tool_success increments success counter."""
        mc = MetricsCollector()
        mc.record_tool_success("read_file")

        summary = mc.dump_summary()
        assert summary["tool_calls"]["total"] == 1
        assert summary["tool_calls"]["successes"] == 1
        assert summary["tool_calls"]["success_rate"] == 1.0

    def test_tool_failure_recorded(self):
        """record_tool_failure increments failure counter."""
        mc = MetricsCollector()
        mc.record_tool_failure("edit_file", "file not found")

        summary = mc.dump_summary()
        assert summary["tool_calls"]["total"] == 1
        assert summary["tool_calls"]["failures"] == 1
        assert summary["tool_calls"]["success_rate"] == 0.0
        assert "file not found" in summary["tool_calls"]["failure_reasons"][0]

    def test_mixed_tool_results(self):
        """Mixed successes and failures produce correct rate."""
        mc = MetricsCollector()
        mc.record_tool_success("a")
        mc.record_tool_success("b")
        mc.record_tool_failure("c", "err")

        summary = mc.dump_summary()
        assert summary["tool_calls"]["total"] == 3
        assert summary["tool_calls"]["successes"] == 2
        assert summary["tool_calls"]["failures"] == 1
        assert summary["tool_calls"]["success_rate"] == pytest.approx(0.667, abs=0.01)

    def test_repair_cycle_recorded(self):
        """repair cycle start and outcome are tracked."""
        mc = MetricsCollector()
        mc.record_repair_cycle_start()
        mc.record_repair_cycle_outcome("fixed")
        mc.record_repair_cycle_start()
        mc.record_repair_cycle_outcome("still_failing", "TypeError in main")

        summary = mc.dump_summary()
        assert summary["repair_cycles"]["total"] == 2
        assert len(summary["repair_cycles"]["outcomes"]) == 2

    def test_flush_to_file_writes_json(self):
        """flush_to_file writes a valid JSON file."""
        mc = MetricsCollector()
        start = mc.record_llm_call_start()
        mc.record_llm_call_end(start, tokens_used=500, node_name="planner")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            mc.output_file = f.name

        try:
            mc.flush_to_file()
            with open(mc.output_file, encoding="utf-8") as f:
                data = json.load(f)
            assert "llm" in data
            assert data["llm"]["total_calls"] == 1
        finally:
            if os.path.exists(mc.output_file):
                os.unlink(mc.output_file)

    def test_recent_llm_records_capped(self):
        """recent_llm_records keeps at most 50 entries."""
        mc = MetricsCollector()
        for i in range(60):
            start = mc.record_llm_call_start()
            mc.record_llm_call_end(start, tokens_used=i, node_name="test")

        assert len(mc.recent_llm_records) == 50
