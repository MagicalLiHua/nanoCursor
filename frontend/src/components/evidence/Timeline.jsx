import React from "react";
import { replayStatusLabel, agentToneFromName } from "../../core/format.js";
import EmptyState from "./EmptyState.jsx";

const FILTER_OPTIONS = [
  { value: "all", label: "全部事件" },
  { value: "agent", label: "Agent 活动" },
  { value: "tool", label: "工具调用" },
  { value: "error", label: "错误" },
];

function matchesFilter(event, filter) {
  if (filter === "all") return true;
  if (filter === "agent") {
    return ["agent_activity", "agent_started", "agent_completed", "agent_failed", "agent_cancelled",
      "ephemeral_agent_spawned", "ephemeral_agent_completed", "ephemeral_agent_updated",
      "agent_run_started", "agent_result_merged", "agent_run_failed",
      "parallel_agents_started", "parallel_agents_completed", "parallel_agent_progress",
      "parallel_agent_result", "parallel_agent_failed"].includes(event.type);
  }
  if (filter === "tool") {
    return event.type === "tool_call_finished";
  }
  if (filter === "error") {
    return event.type === "error" || String(event.content || "").startsWith("Error:");
  }
  return true;
}

function ReplayControls({ replay, onPlay, onPause, onReset, onSpeedChange }) {
  const total = replay.events?.length || 0;
  const index = Math.min(replay.index || 0, total);
  const percent = total ? Math.round((index / total) * 100) : 0;
  const canReplay = total > 0;
  const isPlaying = replay.status === "playing";
  const playLabel = index >= total ? "重放" : "播放";

  return (
    <div className="replay-bar">
      <div className="replay-status">
        <strong>{replayStatusLabel(replay.status)}</strong>
        <span>{index} / {total} 事件</span>
      </div>
      <div className="replay-progress" aria-hidden="true">
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="replay-actions">
        <button className="button secondary" onClick={onPlay} disabled={!canReplay || isPlaying}>{playLabel}</button>
        <button className="button secondary" onClick={onPause} disabled={!isPlaying}>暂停</button>
        <button className="button secondary" onClick={onReset} disabled={!canReplay}>复位</button>
        <label className="replay-speed">
          <span>速度</span>
          <select value={replay.speed || 1} onChange={(e) => onSpeedChange(Number(e.target.value))} disabled={!canReplay}>
            {[0.5, 1, 2, 4].map((s) => <option key={s} value={s}>{s}x</option>)}
          </select>
        </label>
      </div>
    </div>
  );
}

export default function Timeline({ state, eventKind, renderEventCapabilityTrace, replayActions }) {
  const replay = state.replay || {};
  const [filter, setFilter] = React.useState("all");
  const [groupByAgent, setGroupByAgent] = React.useState(false);

  const filteredEvents = (state.events || []).filter((e) => matchesFilter(e, filter));

  let groupedEvents = null;
  if (groupByAgent && filteredEvents.length > 0) {
    const groups = {};
    filteredEvents.forEach((event) => {
      const agent = event.payload?.capability_trace?.agent || event.agent || "Lead";
      if (!groups[agent]) groups[agent] = [];
      groups[agent].push(event);
    });
    groupedEvents = Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
  }

  return (
    <div className="timeline-shell">
      <ReplayControls
        replay={replay}
        onPlay={() => replayActions?.play?.()}
        onPause={() => replayActions?.pause?.()}
        onReset={() => replayActions?.reset?.()}
        onSpeedChange={(speed) => replayActions?.setSpeed?.(speed)}
      />
      <div className="timeline-filters">
        <div className="filter-options">
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`filter-btn ${filter === opt.value ? "active" : ""}`}
              onClick={() => setFilter(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <label className="group-toggle">
          <input
            type="checkbox"
            checked={groupByAgent}
            onChange={(e) => setGroupByAgent(e.target.checked)}
          />
          <span>按 Agent 分组</span>
        </label>
      </div>
      {filteredEvents.length ? (
        <div className="timeline">
          {groupByAgent && groupedEvents ? (
            groupedEvents.map(([agent, events]) => (
              <div key={agent} className="agent-group">
                <div className={`agent-group-header ${agentToneFromName(agent)}`}>
                  <span className="agent-group-name">{agent}</span>
                  <span className="agent-group-count">{events.length} 个事件</span>
                </div>
                {events.map((event, i) => (
                  <article key={i} className={`event-item ${eventKind?.(event.type) || ""}`}>
                    <span className="event-line" />
                    <div>
                      <div className="event-title">{event.title || event.type}</div>
                      <div className="event-content">{event.content || ""}</div>
                      {renderEventCapabilityTrace?.(event)}
                    </div>
                    <time className="event-time">{event.time || ""}</time>
                  </article>
                ))}
              </div>
            ))
          ) : (
            filteredEvents.map((event, i) => (
              <article key={i} className={`event-item ${eventKind?.(event.type) || ""}`}>
                <span className="event-line" />
                <div>
                  <div className="event-title">{event.title || event.type}</div>
                  <div className="event-content">{event.content || ""}</div>
                  {renderEventCapabilityTrace?.(event)}
                </div>
                <time className="event-time">{event.time || ""}</time>
              </article>
            ))
          )}
        </div>
      ) : (
        <EmptyState
          title={filter === "all" ? "等待事件流" : "没有匹配事件"}
          detail={filter === "all" ? "运行开始后，Agent 活动、工具调用和错误事件会实时出现。" : "换一个筛选条件可以查看其他事件。"}
        />
      )}
    </div>
  );
}
