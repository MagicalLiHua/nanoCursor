import React, { useEffect, useMemo, useState } from "react";
import { BrainCircuit, CheckCircle2, ChevronDown, Gauge, RefreshCcw, Scissors, ShieldCheck, Sparkles } from "lucide-react";
import { getApiClient } from "../../core/sharedApi.js";

function isRealThreadId(value = "") {
  const text = String(value || "");
  return text && text !== "pending" && !text.startsWith("draft-") && !text.startsWith("conv-");
}

function formatTokens(value) {
  const number = Number(value || 0);
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(number >= 10_000_000 ? 0 : 1)}M`;
  if (number >= 1_000) return `${Math.round(number / 1_000)}K`;
  return `${number}`;
}

function statusText(status = "") {
  const labels = {
    ok: "充足",
    watch: "观察",
    soft_compact: "需轻压缩",
    hard_compact: "需压缩",
    emergency: "紧急",
  };
  return labels[status] || "待运行";
}

function statusClass(status = "") {
  if (status === "emergency" || status === "hard_compact") return "danger";
  if (status === "soft_compact") return "warn";
  if (status === "watch") return "watch";
  if (status === "ok") return "ok";
  return "idle";
}

function displayModel(model) {
  if (!model) return "模型窗口";
  const provider = model.provider || "";
  const name = model.model || "";
  return [provider, name].filter(Boolean).join(" / ") || "模型窗口";
}

function SectionBar({ section, maxTokens }) {
  const percent = Math.min(100, Math.max(2, Math.round((Number(section.tokens || 0) / Math.max(maxTokens, 1)) * 100)));
  return (
    <div className="context-section-row">
      <div>
        <strong title={section.label || section.id}>{section.label || section.id}</strong>
        <span>{section.category || "context"}</span>
      </div>
      <div className="context-section-meter" aria-hidden="true">
        <i style={{ width: `${percent}%` }} />
      </div>
      <em>{formatTokens(section.tokens)}</em>
    </div>
  );
}

function latestCompactionEvent(events = []) {
  const candidates = Array.isArray(events) ? events : [];
  return [...candidates]
    .reverse()
    .find((event) => [
      "context_compaction_finished",
      "context_compaction_failed",
      "context_compaction_started",
    ].includes(event?.type));
}

function compactionStatusText(type = "") {
  if (type === "context_compaction_finished") return "已完成";
  if (type === "context_compaction_failed") return "失败";
  if (type === "context_compaction_started") return "进行中";
  return "未触发";
}

function summaryModeText(mode = "") {
  if (mode === "llm") return "LLM 摘要";
  return "确定性摘要";
}

function formatRatio(value) {
  const number = Number(value || 0);
  if (!number) return "0%";
  return `${Math.round(number * 100)}%`;
}

function formatSectionName(value = "") {
  const labels = {
    conversation_summary: "会话摘要",
    execution_summary: "运行摘要",
    selected_files: "相关文件",
    file_outlines: "文件大纲",
    tool_results: "工具输出",
    old_agent_activity: "历史 Agent 动态",
    selection_reasons: "选择依据",
    selected_skill_details: "Skills",
    selected_memories: "记忆",
    user_preferences: "偏好",
    omitted: "已裁剪上下文",
  };
  return labels[value] || value;
}

function CompactionCard({ event }) {
  if (!event) return null;
  const payload = event.payload || {};
  const type = event.type || "";
  const finished = type === "context_compaction_finished";
  const failed = type === "context_compaction_failed";
  const sources = Array.isArray(payload.source_section_ids) ? payload.source_section_ids : [];
  const anchors = Array.isArray(payload.preserved_anchors) ? payload.preserved_anchors : [];
  const summaryMode = payload.summary_mode || payload.mode || "";

  return (
    <div className={`context-compaction-card ${finished ? "finished" : failed ? "failed" : "running"}`}>
      <div className="context-compaction-head">
        <Sparkles size={15} />
        <strong>最近自动压缩</strong>
        <span>{summaryMode ? `${compactionStatusText(type)} · ${summaryModeText(summaryMode)}` : compactionStatusText(type)}</span>
      </div>
      <p>{event.content || payload.reason || "上下文压力升高时会自动压缩低优先级历史和工具输出。"}</p>
      <div className="context-compaction-stats">
        <span>
          <small>压缩前</small>
          <strong>{formatTokens(payload.before_tokens)} · {formatRatio(payload.before_usage_ratio)}</strong>
        </span>
        <span>
          <small>压缩后</small>
          <strong>{payload.after_tokens == null ? "等待中" : `${formatTokens(payload.after_tokens)} · ${formatRatio(payload.after_usage_ratio)}`}</strong>
        </span>
      </div>
      {sources.length > 0 && (
        <div className="context-chip-list" aria-label="被压缩的上下文">
          {sources.slice(0, 4).map((source) => (
            <span key={source}>{formatSectionName(source)}</span>
          ))}
          {sources.length > 4 && <span>+{sources.length - 4}</span>}
        </div>
      )}
      {anchors.length > 0 && (
        <div className="context-anchor-line">
          <ShieldCheck size={13} />
          <span>已保留 {anchors.slice(0, 3).map(formatSectionName).join("、")}{anchors.length > 3 ? ` 等 ${anchors.length} 项` : ""}</span>
        </div>
      )}
      {Array.isArray(payload.warnings) && payload.warnings.length > 0 && (
        <div className="context-warning-line" title={payload.warnings.join("\n")}>
          {payload.used_llm ? "LLM 摘要完成，存在提示" : "已自动降级为本地摘要"}
        </div>
      )}
    </div>
  );
}

export default function ContextWindowPanel({ state }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [model, setModel] = useState(null);
  const [ledger, setLedger] = useState(null);
  const [settings, setSettings] = useState({
    summary_mode: "deterministic",
    auto_compact_enabled: true,
    auto_compact_min_level: "hard",
    manual_compact_strategy: "summary",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const threadId = state.currentThreadId;
  const canLoadLedger = isRealThreadId(threadId);

  useEffect(() => {
    let cancelled = false;
    const api = getApiClient();

    async function loadContextUsage() {
      try {
        const currentModel = await api.fetchJson("/api/context/model/current");
        if (!cancelled) setModel(currentModel);
      } catch (err) {
        if (!cancelled) setError(err.message || "上下文配置未载入");
      }

      try {
        const currentSettings = await api.fetchJson("/api/context/compaction/settings");
        if (!cancelled) setSettings((previous) => ({ ...previous, ...currentSettings }));
      } catch (err) {
        if (!cancelled) setError(err.message || "压缩设置未载入");
      }

      if (!canLoadLedger) {
        if (!cancelled) setLedger(null);
        return;
      }

      try {
        const currentLedger = await api.fetchJson(`/api/context/runs/${encodeURIComponent(threadId)}/ledger`);
        if (!cancelled) {
          setLedger(currentLedger);
          setError("");
        }
      } catch (err) {
        if (!cancelled) {
          setLedger(null);
          if (!String(err.message || "").includes("404")) setError(err.message || "上下文账本未载入");
        }
      }
    }

    loadContextUsage();
    return () => {
      cancelled = true;
    };
  }, [threadId, state.status, canLoadLedger, refreshTick]);

  const sections = useMemo(() => {
    const items = Array.isArray(ledger?.sections) ? ledger.sections : [];
    return [...items].sort((a, b) => Number(b.tokens || 0) - Number(a.tokens || 0)).slice(0, 5);
  }, [ledger]);

  const usageRatio = Number(ledger?.usage_ratio || 0);
  const usagePercent = Math.min(100, Math.round(usageRatio * 100));
  const status = ledger?.status || "";
  const cls = statusClass(status);
  const inputTokens = Number(ledger?.input_tokens || 0);
  const usableTokens = Number(ledger?.usable_input_tokens || model?.context_window || 0);
  const compactionEvent = useMemo(() => latestCompactionEvent(state.events), [state.events]);

  async function updateSummaryMode(summaryMode) {
    if (settingsBusy || summaryMode === settings.summary_mode) return;
    const previous = settings;
    setSettings((value) => ({ ...value, summary_mode: summaryMode }));
    setSettingsBusy(true);
    try {
      const api = getApiClient();
      const next = await api.requestJson("/api/context/compaction/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ summary_mode: summaryMode }),
      });
      setSettings((value) => ({ ...value, ...next }));
      setError("");
    } catch (err) {
      setSettings(previous);
      setError(err.message || "压缩设置保存失败");
    } finally {
      setSettingsBusy(false);
    }
  }

  async function compactNow() {
    if (!canLoadLedger || busy) return;
    setBusy(true);
    try {
      const api = getApiClient();
      const result = await api.requestJson(`/api/context/runs/${encodeURIComponent(threadId)}/compact`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          level: status === "emergency" ? "emergency" : "hard",
          reason: "frontend_manual",
          strategy: settings.manual_compact_strategy || "summary",
          summary_mode: settings.summary_mode || "deterministic",
        }),
      });
      if (result.ledger) {
        setLedger(result.ledger);
        setError(`已压缩 ${formatTokens(result.reduced_tokens)} tokens`);
      }
    } catch (err) {
      setError(err.message || "压缩失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="codex-inspector-section context-window-panel">
      <div className="codex-section-head">
        <h4>上下文</h4>
        <button
          type="button"
          className="context-icon-button"
          onClick={() => {
            setError("");
            setRefreshTick((value) => value + 1);
          }}
          title="刷新上下文显示"
        >
          <RefreshCcw size={15} />
        </button>
      </div>

      <div className={`context-window-summary ${cls}`}>
        <div className="context-window-topline">
          <BrainCircuit size={17} />
          <strong title={displayModel(model)}>{displayModel(model)}</strong>
          <span>{statusText(status)}</span>
        </div>
        <div className="context-usage-line">
          <Gauge size={15} />
          <span>{formatTokens(inputTokens)} / {formatTokens(usableTokens)}</span>
          <strong>{usagePercent || 0}%</strong>
        </div>
        <div className="context-progress" aria-label={`上下文使用率 ${usagePercent || 0}%`}>
          <i style={{ width: `${usagePercent || 0}%` }} />
        </div>
      </div>

      <button
        type="button"
        className="context-detail-toggle"
        onClick={() => setDetailsOpen((value) => !value)}
        aria-expanded={detailsOpen}
      >
        <span>
          <strong>上下文明细</strong>
          <small>{sections.length ? `${sections.length} 个主要来源` : "运行后可查看 token 构成"}</small>
        </span>
        <ChevronDown size={15} />
      </button>

      {detailsOpen && (
        <div className="context-detail-body">
          <div className="context-mode-selector" aria-label="摘要压缩模式">
            <button
              type="button"
              className={settings.summary_mode === "deterministic" ? "active" : ""}
              onClick={() => updateSummaryMode("deterministic")}
              disabled={settingsBusy}
            >
              确定性
            </button>
            <button
              type="button"
              className={settings.summary_mode === "llm" ? "active" : ""}
              onClick={() => updateSummaryMode("llm")}
              disabled={settingsBusy}
            >
              LLM
            </button>
          </div>

          {sections.length ? (
            <div className="context-section-list">
              {sections.map((section) => (
                <SectionBar key={section.id} section={section} maxTokens={inputTokens} />
              ))}
            </div>
          ) : (
            <div className="context-window-empty">
              <span>运行后显示 system、历史、文件、工具、记忆等占用。</span>
            </div>
          )}

          <CompactionCard event={compactionEvent} />
        </div>
      )}

      <div className="context-window-actions">
        <button type="button" onClick={compactNow} disabled={!ledger || busy}>
          <Scissors size={14} />
          <span>{busy ? "压缩中" : "压缩上下文"}</span>
        </button>
        {compactionEvent?.type === "context_compaction_finished" && !error && (
          <span className="context-success"><CheckCircle2 size={12} /> 自动压缩可追踪</span>
        )}
        {error && <span title={error}>{error}</span>}
      </div>
    </section>
  );
}
