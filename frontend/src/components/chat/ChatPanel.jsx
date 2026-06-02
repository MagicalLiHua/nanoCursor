import React, { useEffect, useRef, useState } from "react";
import { approvalDecisionLabel, shortId, agentToneFromName, statusLabel } from "../../core/format.js";
import { renderMarkdown } from "../../core/markdown.js";
import { ArrowUp, Code2, FolderSearch, Pencil, Plus, Timer } from "lucide-react";
import AgentActivityStream from "./AgentActivityStream.jsx";
import ToolCallBubble from "./ToolCallBubble.jsx";
const AVATAR_LETTERS = {
  lead: "L",
  planner: "P",
  coder: "<>",
  tester: "T",
  reviewer: "R",
  designer: "D",
  devops: "O",
  user: "U",
};

function AgentAvatar({ name, tone, extraClass = "" }) {
  const safeTone = agentToneFromName(name, tone);
  const letter = AVATAR_LETTERS[safeTone] || String(name || "A").charAt(0).toUpperCase();
  return (
    <div className={`agent-avatar ${safeTone} ${extraClass}`} title={name || safeTone}>
      <span>{letter}</span>
    </div>
  );
}

function WelcomeScreen({ onSubmit }) {
  const [inputValue, setInputValue] = React.useState("");
  const suggestions = [
    { icon: Code2, label: "写代码", prompt: "帮我实现一个小功能，并给出测试说明" },
    { icon: Pencil, label: "改代码", prompt: "帮我阅读当前项目并优化一个可以改进的地方" },
    { icon: FolderSearch, label: "看项目", prompt: "帮我看看这个项目的结构和可以继续完善的点" },
  ];

  const handleSubmit = (e) => {
    e.preventDefault();
    if (inputValue.trim()) {
      onSubmit?.(inputValue.trim());
      setInputValue("");
    }
  };

  return (
    <div className="welcome-screen">
      <div className="welcome-screen-content">
        <h1>你想让 nanoCursor 做什么？</h1>

        <form className="welcome-input-form" onSubmit={handleSubmit}>
          <span className="welcome-input-icon" aria-hidden="true"><Plus size={22} /></span>
          <input
            className="welcome-input"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="描述一个代码任务，或者问问当前项目"
            autoFocus
          />
          <button className="welcome-submit" type="submit" aria-label="发送">
            <ArrowUp size={18} />
          </button>
        </form>
        <div className="welcome-suggestions">
          {suggestions.map((item) => (
            <button key={item.label} type="button" onClick={() => setInputValue(item.prompt)}>
              <item.icon size={16} />
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function normalizeAgentKey(value = "") {
  const clean = String(value || "Lead")
    .replace(/\s*Agent$/i, "")
    .trim()
    .toLowerCase();
  return clean || "lead";
}

function isRealAgentActivity(activity = {}) {
  if (!activity.explicitAgentWork) return false;
  if (!String(activity.text || "").trim()) return false;
  return !["token", "metrics_updated", "assistant_message"].includes(activity.eventType);
}

function latestActivityByAgent(activities = []) {
  const result = new Map();
  for (const activity of activities) {
    if (!isRealAgentActivity(activity)) continue;
    const key = normalizeAgentKey(activity.agent);
    if (!result.has(key)) result.set(key, activity);
  }
  return result;
}

function Message({ message, index, activity }) {
  const isUser = message.role === "user";
  const tone = isUser ? "user" : agentToneFromName(message.author);
  return (
    <article className={`message ${isUser ? "user" : ""}`} data-message-role={message.role} data-message-index={index}>
      <AgentAvatar name={message.author || "用户"} tone={tone} extraClass="avatar" />
      <div className="bubble">
        <div className="message-head">
          <span className="message-author">{isUser ? "用户" : message.author}</span>
          <span className="message-time">{message.time}</span>
        </div>
        {isUser ? (
          <p className="message-text">{message.content}</p>
        ) : (
          <div className="message-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
        )}
        {!isUser && activity && (
          <div className="message-activity-line">
            <AgentActivityStream activities={[activity]} maxItems={1} />
          </div>
        )}
      </div>
    </article>
  );
}

function AgentWorkMessage({ activity }) {
  const agent = activity.agent || "Lead";
  const tone = agentToneFromName(agent);
  return (
    <article className="message assistant agent-work-message" data-message-role="assistant-runtime">
      <AgentAvatar name={agent} tone={tone} extraClass="avatar" />
      <div className="bubble">
        <div className="message-head">
          <span className="message-author">{agent} Agent</span>
          <span className="message-time">{activity.time || "实时"}</span>
        </div>
        <AgentActivityStream activities={[activity]} maxItems={1} />
      </div>
    </article>
  );
}

function ToolCallMessage({ event }) {
  const agent = event.payload?.capability_trace?.agent || event.agent || "Agent";
  const tone = agentToneFromName(agent);
  return (
    <article className="message assistant tool-message" data-message-role="tool-call">
      <AgentAvatar name={agent} tone={tone} extraClass="avatar" />
      <div className="bubble">
        <div className="message-head">
          <span className="message-author">{agent}</span>
          <span className="message-time">{event.time || ""}</span>
        </div>
        <ToolCallBubble event={event} />
      </div>
    </article>
  );
}

function ApprovalPanel({ state, onDecision, onCommentChange }) {
  const approval = state.approval || {};
  if (!approval.status || approval.status === "idle" || approval.status === "resolved") return null;

  const tasks = approval.tasks || [];
  const isPending = approval.status === "pending";
  const isToolApproval = approval.kind === "tool";

  return (
    <section className={`approval-panel ${approval.status}`}>
      <div className="approval-head">
        <div>
          <span className="approval-kicker">{isToolApproval ? "工具审批" : "计划审批"}</span>
          <h3>{approval.title || "等待用户审批计划"}</h3>
        </div>
        <span className={`badge ${isPending ? "warning" : approval.decision || "ready"}`}>
          {isPending ? "待审批" : approvalDecisionLabel(approval.decision)}
        </span>
      </div>
      <p>{approval.content || ""}</p>
      {tasks.length > 0 && (
        <div className="approval-tasks">
          {tasks.slice(0, 4).map((task, i) => (
            <div key={i} className="approval-task">
              <strong>{i + 1}</strong>
              <span>{task.title || task.id || task}</span>
            </div>
          ))}
        </div>
      )}
      {isPending ? (
        <>
          <textarea
            className="approval-comment"
            id="approval-comment"
            rows="2"
            placeholder="可选：给 Planner 留下审批意见"
            value={state.approvalComment || ""}
            onChange={(e) => onCommentChange?.(e.target.value)}
          />
          <div className="approval-actions">
            <button className="button" onClick={() => onDecision?.("approved")} type="button">批准</button>
            {!isToolApproval && <button className="button secondary" onClick={() => onDecision?.("revise")} type="button">修改</button>}
            <button className="button secondary" onClick={() => onDecision?.("rejected")} type="button">拒绝</button>
          </div>
        </>
      ) : (
        <div className="approval-result">{approval.comment || approvalDecisionLabel(approval.decision)}</div>
      )}
    </section>
  );
}

function RunTimer({ runStartedAt }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!runStartedAt) return;
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - runStartedAt) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [runStartedAt]);
  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  return (
    <span className="pill run-timer-pill">
      <Timer size={13} />
      <strong>{mm}:{ss}</strong>
    </span>
  );
}

export default function ChatPanel({ state, isActionBusy, onSubmit, onCancel, onFillPrompt, onApprovalDecision, onApprovalCommentChange }) {
  const [draft, setDraft] = useState(state.prompt || "");
  const messageListRef = useRef(null);
  const previousRunningRef = useRef(false);
  const previousMessageCountRef = useRef(0);
  const running = ["running", "waiting_approval", "cancelling"].includes(state.status);
  const cancelling = state.status === "cancelling";
  const statusClass = state.status === "running" || state.status === "replaying" ? "running"
    : state.status === "failed" ? "error"
      : state.status === "completed" ? "success"
        : state.status === "waiting_approval" ? "warning"
          : state.status === "cancelling" ? "warning" : "";
  const sessionLabel =
    state.currentConversationId
      ? shortId(state.currentConversationId, "Draft")
      : state.currentThreadId && state.currentThreadId !== "pending"
        ? shortId(state.currentThreadId, "Draft")
        : "Draft";

  const messages = state.messages || [];
  const streamingContent = state.streamingContent || "";
  const isIdle = state.status === "idle";
  const hasUserMessage = messages.some((m) => m.role === "user");
  const showWelcome = isIdle && !hasUserMessage && messages.length <= 1;
  const showInlineActivity = ["running", "waiting_approval", "cancelling"].includes(state.status);
  const activityByAgent = showInlineActivity ? latestActivityByAgent(state.agentActivities || []) : new Map();
  const representedAgentKeys = new Set(
    messages
      .filter((message) => message.role === "assistant")
      .map((message) => normalizeAgentKey(message.author)),
  );
  const unattachedActivities = Array.from(activityByAgent.entries())
    .filter(([agentKey]) => !representedAgentKeys.has(agentKey))
    .map(([, activity]) => activity)
    .slice(0, 3);

  useEffect(() => {
    setDraft(state.prompt || "");
  }, [state.prompt]);

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) return;

    if (running) {
      requestAnimationFrame(() => {
        list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
      });
    }
  }, [
    running,
    messages.length,
    streamingContent,
    state.agentActivities?.length,
    state.events?.length,
  ]);

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) return;

    const wasRunning = previousRunningRef.current;
    const previousMessageCount = previousMessageCountRef.current;
    const lastMessage = messages[messages.length - 1];
    const finalAssistantAdded =
      !running &&
      state.status === "completed" &&
      lastMessage?.role === "assistant" &&
      messages.length > previousMessageCount;
    const justCompleted = wasRunning && !running && state.status === "completed";

    if (justCompleted || finalAssistantAdded) {
      requestAnimationFrame(() => {
        const assistantMessages = list.querySelectorAll('[data-message-role="assistant"]');
        const target = assistantMessages[assistantMessages.length - 1];
        if (target) {
          target.scrollIntoView({ block: "start", behavior: "smooth" });
        }
      });
    }

    previousRunningRef.current = running;
    previousMessageCountRef.current = messages.length;
  }, [running, state.status, messages]);

  const handleComposerSubmit = (e) => {
    e.preventDefault();
    const prompt = draft.trim();
    if (!prompt) return;
    setDraft("");
    onSubmit?.(prompt);
  };

  if (showWelcome) {
    return (
      <section className="panel chat-panel welcome-mode">
        <WelcomeScreen onSubmit={onSubmit} />
      </section>
    );
  }

  return (
    <section className="panel chat-panel">
      <div className="panel-header chat-workbar">
        <div className="chat-title-block">
          <h2 className="panel-title">工作会话</h2>
          <span className="panel-subtitle">{sessionLabel}</span>
        </div>
        <div className="chat-workbar-meta">
          <span className={`pill status-pill status-${statusClass}`}>
            <span className={`status-dot ${statusClass}`} />
            <strong>{statusLabel(state.status)}</strong>
          </span>
          {["running", "waiting_approval", "cancelling"].includes(state.status) && state.runStartedAt && (
            <RunTimer runStartedAt={state.runStartedAt} />
          )}
        </div>
      </div>
      <div className="chat-body">
        <div className="message-list" id="message-list" ref={messageListRef}>
          {messages.map((msg, i) => {
            const activity = msg.role === "assistant"
              ? activityByAgent.get(normalizeAgentKey(msg.author))
              : null;
            return <Message key={i} message={msg} index={i} activity={activity} />;
          })}
          {running && unattachedActivities.map((activity, i) => (
            <AgentWorkMessage key={`${activity.agent}-${activity.time}-${i}`} activity={activity} />
          ))}
          {running && (state.events || [])
            .filter((e) => e.type === "tool_call_finished")
            .slice(-3)
            .map((e, i) => <ToolCallMessage key={`tool-${i}`} event={e} />)
          }
          {streamingContent && (
            <div className="message assistant streaming">
              <AgentAvatar name="Lead" tone="lead" extraClass="avatar" />
              <div className="bubble">
                <div className="message-head">
                  <span className="message-author">Lead Agent</span>
                  <span className="message-time">输出中...</span>
                </div>
                <div className="message-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(streamingContent) }} />
                <span className="streaming-cursor">▊</span>
              </div>
            </div>
          )}
        </div>
        <ApprovalPanel state={state} onDecision={onApprovalDecision} onCommentChange={onApprovalCommentChange} />
        <form className="prompt-box" id="prompt-form" onSubmit={handleComposerSubmit}>
          <div className="composer-toolbar">
            <div className="composer-modes" aria-label="任务模式">
              <span className="composer-mode active">Agent</span>
              <span className="composer-mode">Edit</span>
              <span className="composer-mode">Review</span>
            </div>
            <span className="composer-hint">Enter 发送 · Shift + Enter 换行</span>
          </div>
          <textarea
            className="prompt-input"
            id="prompt-input"
            rows="1"
            placeholder="描述你想让 nanoCursor 完成的代码任务"
            title="Enter 发送，Shift + Enter 换行"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.target.closest("form")?.requestSubmit();
              }
            }}
          />
          {running ? (
            <button
              className={`button secondary ${isActionBusy?.("cancel-run") ? "loading" : ""}`}
              onClick={onCancel}
              type="button"
              disabled={cancelling || isActionBusy?.("cancel-run")}
            >
              {cancelling ? "取消中" : "停止"}
            </button>
          ) : (
            <button
              className={`button ${isActionBusy?.("run-prompt") ? "loading" : ""}`}
              type="submit"
              disabled={isActionBusy?.("run-prompt")}
            >
              {isActionBusy?.("run-prompt") ? "连接中" : "发送"}
            </button>
          )}
        </form>
      </div>
    </section>
  );
}
