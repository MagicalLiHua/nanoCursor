/**
 * ChatPage — CLI-style chat interface
 *
 * Layout: fixed header + scrollable messages + fixed input at bottom.
 * Messages are left-aligned with role-indicator left borders.
 * Assistant messages render as Markdown with syntax-highlighted code blocks.
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { useApp } from '../context/AppContext';
import { startRun, cancelRun, listFiles, getConfig, getMetrics, runBash, setWorkspace } from '../api/client';

/* ================================================================
   Markdown Renderer
   ================================================================ */

function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        components={{
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || '');
            const codeStr = String(children).replace(/\n$/, '');
            // inline code (no language class)
            if (!match) {
              return <code className="inline-code" {...props}>{children}</code>;
            }
            return (
              <div className="code-block-wrap">
                <div className="code-block-lang">{match[1]}</div>
                <SyntaxHighlighter
                  style={oneDark}
                  language={match[1]}
                  PreTag="div"
                >
                  {codeStr}
                </SyntaxHighlighter>
              </div>
            );
          },
          // Open links in new tab
          a({ href, children }) {
            return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

/* ================================================================
   Chat Input — textarea that auto-grows
   ================================================================ */

function ChatInput({ onSubmit, disabled }: { onSubmit: (p: string) => void; disabled: boolean }) {
  const [prompt, setPrompt] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  function adjustHeight() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }

  useEffect(() => { adjustHeight(); }, [prompt]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim() || disabled) return;
    onSubmit(prompt.trim());
    setPrompt('');
    // Reset height
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    // Enter submits, Shift+Enter inserts newline
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  }

  return (
    <form className="chat-input-form" onSubmit={handleSubmit}>
      <textarea
        ref={textareaRef}
        className="chat-input-textarea"
        rows={3}
        placeholder={disabled ? 'Running...' : 'Send a message (Enter to send, Shift+Enter for newline)'}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
      />
    </form>
  );
}

/* ================================================================
   Chat Page
   ================================================================ */

export function ChatPage() {
  const { state, dispatch, setWorkspaceDir } = useApp();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const isSubmittingRef = useRef(false);

  const [workspacePath, setWorkspacePath] = useState(
    () => localStorage.getItem('nanoCursor-workspaceDir') || ''
  );
  const [welcomePrompt, setWelcomePrompt] = useState('');
  const [isEntering, setIsEntering] = useState(false);
  const [pathError, setPathError] = useState('');
  const [toolCallCount, setToolCallCount] = useState(0);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.chatMessages]);

  // Reset isEntering when workspaceDir is cleared (e.g. "新建对话")
  useEffect(() => {
    if (!state.workspaceDir) {
      setIsEntering(false);
    }
  }, [state.workspaceDir]);

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  const handleCancel = useCallback(async () => {
    if (!state.isRunning || !state.threadId) return;
    try { await cancelRun(state.threadId); } catch { /* best effort */ }
  }, [state.isRunning, state.threadId]);

  const handleCommand = useCallback(async (cmd: string): Promise<boolean> => {
    const args = cmd.trim().split(/\s+/);
    const name = args[0].toLowerCase();

    function reply(content: string) {
      dispatch({ type: 'ADD_CHAT_MESSAGE', payload: { role: 'assistant', content } });
    }

    switch (name) {
      case '/help':
        reply(
          '**Available Commands**\n\n' +
          '| Command | Description |\n|---------|------------|\n' +
          '| `/help` | Show this help |\n' +
          '| `/clear` | Clear chat history |\n' +
          '| `/files` | List workspace files |\n' +
          '| `/config` | Show LLM provider config |\n' +
          '| `/metrics` | Show current metrics |\n' +
          '| `/bash` | Run shell command in workspace |\n' +
          '| `/cancel` | Cancel running task |\n' +
          '| `/workspace` | Show workspace path |\n' +
          '\nPrefix with `/` to run a command, otherwise send to agent.'
        );
        return true;

      case '/clear':
        dispatch({ type: 'CLEAR_CHAT' });
        dispatch({ type: 'SET_THREAD_ID', payload: crypto.randomUUID() });
        reply('Chat cleared. New thread started.');
        return true;

      case '/files':
        try {
          const d = await listFiles();
          const lines = d.files.slice(0, 30).map((f: any) =>
            `${f.isDir ? '  📁' : '  📄'} ${f.path}${!f.isDir ? ` (${f.size}b)` : ''}`
          );
          reply(`**Workspace files** (${d.files.length} total):\n\`\`\`\n${lines.join('\n')}\n\`\`\``);
        } catch (e: any) {
          reply(`Failed: ${e.message}`);
        }
        return true;

      case '/config':
        try {
          const cfg = await getConfig();
          const provs = Object.entries(cfg.llmProviders as Record<string, { hasKey: boolean; model: string; baseUrl?: string }>)
            .map(([k, v]) => `${k}: ${v.hasKey ? '✅ ' + v.model : '❌ no key'}`)
            .join('\n');
          reply(`**LLM Providers**\n\n${provs}\n\nWorkspace: \`${cfg.system.workspace_dir || '?'}\``);
        } catch (e: any) {
          reply(`Failed: ${e.message}`);
        }
        return true;

      case '/metrics':
        try {
          const m = await getMetrics();
          const c = (m as any).current || {};
          const llm = c.llm || {};
          const tc = c.tool_calls_detail || c.tool_calls || {};
          reply(
            `**Current Metrics**\n\n` +
            `| Metric | Value |\n|--------|------|\n` +
            `| LLM Calls | ${llm.total_calls || 0} |\n` +
            `| Total Tokens | ${(llm.total_tokens || 0).toLocaleString()} |\n` +
            `| Avg Latency | ${(llm.avg_latency_ms || 0).toFixed(0)}ms |\n` +
            `| Tool Calls | ${tc.total || 0} |\n` +
            `| Tool Success | ${((tc.success_rate || 0) * 100).toFixed(0)}% |`
          );
        } catch (e: any) {
          reply(`Failed: ${e.message}`);
        }
        return true;

      case '/cancel':
        if (!state.isRunning) {
          reply('No task is running.');
        } else {
          await handleCancel();
          reply('Cancel requested.');
        }
        return true;

      case '/workspace':
        reply(`Workspace: \`${state.workspaceDir || '(not set)'}\`\nThread: \`${state.threadId.slice(0, 8)}\``);
        return true;

      case '/bash': {
        const bashCmd = args.slice(1).join(' ');
        if (!bashCmd) {
          reply('Usage: `/bash <command>` — run a shell command in the workspace directory.');
          return true;
        }
        reply(`\`$ ${bashCmd}\``);
        try {
          const result = await runBash(bashCmd, state.workspaceDir);
          const out = result.stdout || '';
          const err = result.stderr || '';
          let output = '';
          if (out && out !== '(no output)') output += out;
          if (err) output += (output ? '\n\n**stderr:**\n```\n' : '') + err + (output ? '\n```' : '');
          if (!output && result.success) output = '(no output)';
          const label = result.success ? '✓' : `✗ exit: ${result.exit_code}`;
          reply(`**${label}**\n\`\`\`\n${output.slice(0, 10000)}\n\`\`\``);
        } catch (e: any) {
          reply(`Bash failed: ${e.message}`);
        }
        return true;
      }

      default:
        reply(`Unknown command: \`${name}\`. Type \`/help\` to see available commands.`);
        return true;
    }
  }, [dispatch, state.isRunning, state.workspaceDir, state.threadId]);

  const handleSendPrompt = useCallback(async (prompt: string, workspaceDir?: string) => {
    if (isSubmittingRef.current) return;

    // Intercept slash commands
    if (prompt.startsWith('/')) {
      dispatch({ type: 'ADD_CHAT_MESSAGE', payload: { role: 'user', content: prompt } });
      await handleCommand(prompt);
      return;
    }

    isSubmittingRef.current = true;

    dispatch({ type: 'ADD_CHAT_MESSAGE', payload: { role: 'user', content: prompt } });
    dispatch({ type: 'SET_SIDEBAR_METRICS' as any, payload: { llm_calls: 0, total_tokens: 0, tool_success_rate: 0 } });

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setToolCallCount(0);
    dispatch({ type: 'SET_RUNNING', payload: true });

    try {
      const result = await startRun(prompt, state.threadId, workspaceDir || state.workspaceDir);
      const threadId = result.thread_id;

      const eventSource = new EventSource(`/api/run/${threadId}/events`);
      eventSourceRef.current = eventSource;

      eventSource.addEventListener('tool_call', (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data);
          const tool = data.tool || '';
          const output = data.output || '';
          const line = output.slice(0, 200);
          dispatch({
            type: 'ADD_CHAT_MESSAGE',
            payload: { role: 'assistant', content: `\`[${tool}]\` ${line}` },
          });
          setToolCallCount((c) => c + 1);
          // Extract real-time metrics from each tool_call event
          const m = data.metrics;
          if (m) {
            const llm = m.llm || {};
            const tc = m.tool_calls || {};
            dispatch({
              type: 'SET_SIDEBAR_METRICS',
              payload: {
                llm_calls: llm.total_calls || 0,
                total_tokens: llm.total_tokens || 0,
                tool_success_rate: tc.success_rate || 0,
              },
            });
          }
        } catch { /* ignore parse errors */ }
      });

      eventSource.addEventListener('node_update', (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data);
          const content = data.data?.content || '';
          if (content) {
            dispatch({ type: 'ADD_CHAT_MESSAGE', payload: { role: 'assistant', content } });
          }
          // Extract real-time metrics from SSE event
          const metrics = data.data?.metrics;
          if (metrics) {
            const llm = metrics.llm || {};
            const tc = metrics.tool_calls || {};
            dispatch({
              type: 'SET_SIDEBAR_METRICS',
              payload: {
                llm_calls: llm.total_calls || 0,
                total_tokens: llm.total_tokens || 0,
                tool_success_rate: tc.success_rate || 0,
              },
            });
          }
        } catch { /* ignore parse errors */ }
      });

      eventSource.addEventListener('done', () => {
        eventSource.close();
        if (eventSourceRef.current === eventSource) eventSourceRef.current = null;
        dispatch({ type: 'SET_RUNNING', payload: false });
      });

      eventSource.onerror = () => {
        eventSource.close();
        if (eventSourceRef.current === eventSource) eventSourceRef.current = null;
        dispatch({ type: 'SET_RUNNING', payload: false });
        dispatch({
          type: 'ADD_CHAT_MESSAGE',
          payload: { role: 'assistant', content: 'Connection interrupted. Please check backend status.' },
        });
      };
    } catch (err: any) {
      dispatch({ type: 'SET_RUNNING', payload: false });
      dispatch({
        type: 'ADD_CHAT_MESSAGE',
        payload: { role: 'assistant', content: `Start failed: ${err?.message ?? 'Unknown error'}` },
      });
    } finally {
      isSubmittingRef.current = false;
    }
  }, [dispatch, state.threadId, state.workspaceDir]);

  /* ---------- Welcome screen (no workspace set) ---------- */
  if (!state.workspaceDir && !isEntering) {
    return (
      <div className="welcome-screen">
        <div className="welcome-content">
          <div className="welcome-logo-wrap">
            <div className="welcome-logo">
              <svg viewBox="0 0 24 24" fill="none" width="36" height="36">
                <circle cx="12" cy="12" r="4" fill="white" opacity="0.95" />
                <circle cx="12" cy="12" r="9" stroke="white" strokeWidth="1.5" opacity="0.5" />
                <circle cx="12" cy="12" r="2" fill="white" />
              </svg>
            </div>
            <div className="welcome-logo-pulse" />
          </div>

          <div className="welcome-header">
            <h1>nanoCursor</h1>
            <p>LLM Tool Calling auto-programming framework</p>
          </div>

          <div className="welcome-form-wrap">
            <div className="welcome-form-label">Workspace directory (required)</div>
            <input
              type="text"
              className="workspace-path-input"
              placeholder="e.g. E:\projects\myapp — agent will read/write files here"
              value={workspacePath}
              onChange={(e) => { setWorkspacePath(e.target.value); setPathError(''); }}
            />
            {pathError && <div className="welcome-error">{pathError}</div>}

            <form
              className="welcome-chat-form"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!workspacePath.trim()) { setPathError('Please enter a workspace directory'); return; }
                setWorkspaceDir(workspacePath.trim());
                // Sync workspace to backend so /files, /bash etc. use the right directory
                try { await setWorkspace(workspacePath.trim()); } catch { /* best effort */ }
                if (welcomePrompt.trim()) {
                  setIsEntering(true);
                  handleSendPrompt(welcomePrompt.trim(), workspacePath.trim());
                } else {
                  setIsEntering(true);
                }
              }}
            >
              <input
                type="text"
                className="welcome-chat-input"
                placeholder="Enter your task (optional, can skip)"
                value={welcomePrompt}
                onChange={(e) => setWelcomePrompt(e.target.value)}
              />
              <button type="submit" className="welcome-send-btn" disabled={!workspacePath.trim()}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13" />
                  <polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            </form>
            <p className="workspace-hint">Enter a valid workspace path to continue. The agent needs this to know where to work.</p>
          </div>
        </div>
      </div>
    );
  }

  /* ---------- Chat view ---------- */
  return (
    <div className="chat-main">
      {/* Fixed header */}
      <div className="chat-page-header">
        <h2>Terminal</h2>
        <div className="chat-header-meta">
          {state.isRunning && (
            <span className="running-indicator">
              <span className="running-dot" />
              Running... {toolCallCount} tool calls
            </span>
          )}
          {state.isRunning && (
            <button className="cancel-btn-inline" onClick={handleCancel}>Cancel</button>
          )}
        </div>
      </div>

      {/* Scrollable messages */}
      <div className="chat-messages" ref={messagesContainerRef}>
        {state.chatMessages.length === 0 && (
          <div className="chat-empty-hint">
            <p>Send a message to start a task.</p>
            <p className="hint-sub">The agent can read/write files and execute commands in the workspace.</p>
          </div>
        )}
        {state.chatMessages.map((msg, i) => (
          <div key={i} className={`chat-message ${msg.role}`}>
            <div className="message-role-label">
              {msg.role === 'user' ? '▸' : '◉'}
            </div>
            <div className="message-body">
              {msg.role === 'assistant' ? (
                <MarkdownRenderer content={msg.content} />
              ) : (
                <div className="message-text">{msg.content}</div>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Fixed input area at bottom */}
      <div className="chat-input-area">
        <ChatInput onSubmit={(p) => handleSendPrompt(p)} disabled={state.isRunning} />
      </div>
    </div>
  );
}
