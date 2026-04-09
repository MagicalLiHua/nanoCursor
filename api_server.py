"""
nanoCursor API Server - FastAPI 后端

提供给 React 前端的 REST + SSE 接口，替代原来的 Streamlit web_ui.py。
主要功能：
- 启动 LangGraph 工作流并流式返回事件 (SSE)
- 提供文件浏览、指标、配置等数据接口
"""

import os
import sys
import json
import uuid
import time
import asyncio
import threading
import queue
from pathlib import Path
from typing import Dict, Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

# ============================================================
# 导入项目模块
# ============================================================

# 确保项目根目录在 sys.path 中
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 加载环境变量
load_dotenv(os.path.join(ROOT, "src", "core", ".env"))

# 导入 LangGraph 应用、指标收集器、配置等
from run import app as graph_app
from src.core.metrics import metrics as metrics_collector
from src.core.config import WORKSPACE_DIR

# ============================================================
# 创建 FastAPI 应用
# ============================================================

app = FastAPI(
    title="nanoCursor API",
    description="nanoCursor 智能体框架的后端 API 服务",
    version="2.0.0",
)

# 配置 CORS，允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地开发，生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 活跃运行管理
# ============================================================

# 存储每个线程的运行状态和事件队列
# 结构: { thread_id: { "queue": Queue, "status": str, "thread": Thread } }
active_runs: Dict[str, Dict[str, Any]] = {}
# 线程锁，保护 active_runs 的并发访问
runs_lock = threading.Lock()


def _run_workflow(thread_id: str, initial_messages: list, max_retries: int = 3, max_coder_steps: int = 15):
    """
    在后台线程中运行 LangGraph 工作流。

    由于图中的 coder_node 和 reviewer_node 是 async def，需要使用 asyncio.run()
    在独立线程的事件循环中与 astream() 配合运行。

    参数:
        thread_id: 会话的唯一标识符
        initial_messages: 用户输入的对话消息列表
        max_retries: 沙盒测试最大重试次数
        max_coder_steps: Coder 节点最大工具调用步数
    """
    asyncio.run(
        _run_workflow_async(thread_id, initial_messages, max_retries, max_coder_steps)
    )


async def _run_workflow_async(thread_id: str, initial_messages: list, max_retries: int, max_coder_steps: int):
    """_run_workflow 的异步内部实现。"""
    run_info = active_runs.get(thread_id)
    if not run_info:
        return

    q = run_info["queue"]
    config = {"configurable": {"thread_id": thread_id}}

    # 构建初始状态，包含用户消息和配置参数
    initial_state = {
        "messages": [HumanMessage(content=msg) for msg in initial_messages],
        "max_retries": max_retries,
        "retry_count": 0,
        "max_coder_steps": max_coder_steps,
    }

    try:
        # 使用 astream 异步流式获取每个节点的事件
        async for event in graph_app.astream(initial_state, config=config, stream_mode="updates"):
            for node_name, node_state in event.items():
                # 提取每个节点的关键数据，简化后发送给前端
                event_data = _extract_node_event(node_name, node_state)
                q.put(json.dumps({"type": "node_update", "node": node_name, "data": event_data}, ensure_ascii=False))

        # 工作流正常结束
        q.put(json.dumps({"type": "done", "status": "completed"}, ensure_ascii=False))

    except Exception as e:
        # 工作流执行出错，发送错误事件
        q.put(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False))
    finally:
        # 更新运行状态为已完成
        with runs_lock:
            if thread_id in active_runs:
                active_runs[thread_id]["status"] = "completed"


def _extract_node_event(node_name: str, node_state: dict) -> dict:
    """
    从节点状态中提取关键信息，简化后返回给前端。

    不同的节点返回不同的数据字段，这个函数负责统一格式。

    参数:
        node_name: 节点名称 (planner, coder, sandbox, reviewer 等)
        node_state: 节点返回的状态字典

    返回:
        简化后的事件数据字典
    """
    data = {}

    if node_name == "planner":
        # Planner 节点返回当前计划
        data["current_plan"] = node_state.get("current_plan", "")

    elif node_name == "coder":
        # Coder 节点返回最新消息内容
        if "messages" in node_state and node_state["messages"]:
            last_msg = node_state["messages"][-1]
            content = last_msg.content
            # 处理流式格式（list of content blocks）和空内容
            if isinstance(content, list):
                # 提取 text 类型的块
                text_parts = [
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            if content and isinstance(content, str):
                data["content"] = content

    elif node_name == "coder_step_counter":
        # Coder 步数计数器
        data["coder_step_count"] = node_state.get("coder_step_count", 0)

    elif node_name == "sandbox":
        # 沙盒节点返回错误跟踪和重试信息
        data["error_trace"] = node_state.get("error_trace", "")
        data["retry_count"] = node_state.get("retry_count", 0)
        data["max_retries"] = node_state.get("max_retries", 3)

    elif node_name == "reviewer":
        # Reviewer 节点返回诊断内容
        if "messages" in node_state and node_state["messages"]:
            last_msg = node_state["messages"][-1]
            content = last_msg.content
            if isinstance(content, list):
                text_parts = [
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            if content and isinstance(content, str):
                data["content"] = content

    # 每个 node_update 附带上实时指标，前端据此更新侧边栏
    data["metrics"] = metrics_collector.dump_summary()
    return data


# ============================================================
# API 路由
# ============================================================

@app.post("/api/run")
async def start_run(request: dict):
    """
    启动一个新的工作流运行。

    接收用户提示，创建新的线程 ID，在后台启动 LangGraph 工作流。

    请求体:
        {
            "prompt": "用户输入的需求描述",
            "thread_id": "可选的已有线程 ID，用于继续对话"
        }

    返回:
        {
            "thread_id": "会话线程 ID",
            "status": "started"
        }
    """
    prompt = request.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    # 使用已有的 thread_id 或创建新的
    thread_id = request.get("thread_id") or str(uuid.uuid4())

    # 构建初始消息
    initial_messages = [prompt]

    # 如果继续使用已完成的会话，将新 prompt 追加到历史消息末尾以续跑
    if request.get("thread_id"):
        config = {"configurable": {"thread_id": thread_id}}
        try:
            saved_state = graph_app.get_state(config)
            if saved_state and saved_state.values:
                existing_messages = saved_state.values.get("messages", [])
                if existing_messages:
                    initial_messages = existing_messages + [HumanMessage(content=prompt)]
                    print(f"[API] 续跑会话: thread_id={thread_id}, 已追加新消息 (现有 {len(existing_messages)} 条历史消息)")
        except Exception as e:
            print(f"[API] 未能加载历史状态，开始新会话: {e}")

    # 创建事件队列
    q = queue.Queue()

    with runs_lock:
        active_runs[thread_id] = {
            "queue": q,
            "status": "running",
            "thread": None,
        }

    # 在后台线程中启动工作流
    t = threading.Thread(
        target=_run_workflow,
        args=(thread_id, initial_messages),
        daemon=True,
    )
    active_runs[thread_id]["thread"] = t
    t.start()

    return {"thread_id": thread_id, "status": "started"}


@app.get("/api/run/{thread_id}/events")
async def stream_events(thread_id: str):
    """
    SSE (Server-Sent Events) 端点，流式返回工作流事件。

    前端通过 EventSource 连接此端点，实时接收节点执行状态。

    事件格式:
        event: node_update
        data: {"type": "node_update", "node": "planner", "data": {...}}

        event: done
        data: {"type": "done", "status": "completed"}

        event: error
        data: {"type": "error", "message": "错误信息"}

    参数:
        thread_id: 会话线程 ID

    返回:
        text/event-stream 格式的 SSE 事件流
    """
    # 获取运行信息
    run_info = active_runs.get(thread_id)
    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该线程的运行记录")

    q = run_info["queue"]

    def event_generator():
        """生成 SSE 事件流的生成器函数。"""
        while True:
            try:
                # 从队列中获取事件，设置超时避免永久阻塞
                item = q.get(timeout=300)  # 5 分钟超时

                if item is None:
                    # None 表示流结束
                    break

                # 按照 SSE 格式发送事件
                event_type = json.loads(item).get("type", "message")
                yield f"event: {event_type}\ndata: {item}\n\n"

                # 如果是 done 或 error 事件，结束流
                if event_type in ("done", "error"):
                    break

            except queue.Empty:
                # 超时，发送心跳保持连接
                yield ": heartbeat\n\n"
                continue
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


@app.get("/api/run/{thread_id}/state")
async def get_run_state(thread_id: str):
    """
    获取指定线程的当前状态（最终状态）。

    调用 LangGraph 的 get_state 方法获取完整的黑板状态。

    参数:
        thread_id: 会话线程 ID

    返回:
        完整的 AgentState 状态字典
    """
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = graph_app.get_state(config).values

        # 将 LangChain 消息对象转换为可序列化的字典
        result = {}
        for key, value in state.items():
            if key == "messages" and value:
                # 将消息对象转换为 {"role": ..., "content": ...} 格式
                result[key] = [
                    {"role": m.type, "content": m.content}
                    for m in value
                ]
            else:
                result[key] = value

        return result

    except Exception as e:
        raise HTTPException(status_code=404, detail=f"获取状态失败: {str(e)}")


@app.get("/api/files")
async def list_files():
    """
    列出工作区中的所有文件和目录树。

    扫描 WORKSPACE_DIR，返回文件树结构，排除 .backups 和 .snapshots 目录。

    返回:
        {
            "files": [
                {"path": "relative/path", "is_dir": true/false, "size": 1234},
                ...
            ]
        }
    """
    files = []

    try:
        for root, dirs, filenames in os.walk(WORKSPACE_DIR):
            # 排除备份和快照目录
            dirs[:] = [d for d in dirs if d not in (".backups", ".snapshots")]

            for filename in filenames:
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, WORKSPACE_DIR)

                try:
                    stat = os.stat(filepath)
                    files.append({
                        "path": relpath,
                        "is_dir": False,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
                except OSError:
                    pass

            # 也添加目录节点
            for dirname in dirs:
                dirpath = os.path.join(root, dirname)
                relpath = os.path.relpath(dirpath, WORKSPACE_DIR)
                files.append({
                    "path": relpath,
                    "is_dir": True,
                    "size": 0,
                })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取工作区失败: {str(e)}")

    # 按路径排序，方便前端展示
    files.sort(key=lambda f: f["path"])

    return {"files": files}


@app.get("/api/files/{file_path:path}")
async def read_file(file_path: str):
    """
    读取指定文件的内容。

    参数:
        file_path: 相对于 WORKSPACE_DIR 的文件路径

    返回:
        {
            "content": "文件内容",
            "size": 1234,
            "lines": 42,
            "mtime": 1234567890.0,
            "lang": "python"
        }
    """
    # 构建完整文件路径
    full_path = os.path.join(WORKSPACE_DIR, file_path)

    # 安全检查：防止路径遍历攻击
    real_path = os.path.realpath(full_path)
    real_root = os.path.realpath(WORKSPACE_DIR)
    if not real_path.startswith(real_root):
        raise HTTPException(status_code=403, detail="禁止访问该路径")

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    if os.path.isdir(full_path):
        raise HTTPException(status_code=400, detail="这是一个目录，不是文件")

    try:
        stat = os.stat(full_path)

        # 尝试读取文件内容
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            # 如果是二进制文件，返回提示
            content = "[二进制文件，无法显示内容]"

        # 根据扩展名推断语言
        ext = os.path.splitext(file_path)[1].lower()
        lang_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".html": "html",
            ".css": "css",
            ".json": "json",
            ".md": "markdown",
            ".txt": "text",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".sh": "bash",
            ".go": "go",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".rs": "rust",
        }
        lang = lang_map.get(ext, "text")

        return {
            "content": content,
            "size": stat.st_size,
            "lines": content.count("\n") + 1,
            "mtime": stat.st_mtime,
            "lang": lang,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")


@app.get("/api/metrics")
async def get_metrics():
    """
    获取指标数据。

    从 MetricsCollector 单例获取当前运行指标，
    同时读取 workspace/metrics.json 获取历史数据。

    返回:
        {
            "current": { ... },  # 当前指标
            "historical": [...]   # 历史指标记录
        }
    """
    # 获取当前指标
    summary = metrics_collector.dump_summary()

    # 尝试读取历史数据
    historical = []
    metrics_file = os.path.join(WORKSPACE_DIR, "metrics.json")
    if os.path.exists(metrics_file):
        try:
            with open(metrics_file, "r", encoding="utf-8") as f:
                historical = json.load(f)
        except Exception:
            pass

    return {
        "current": summary,
        "historical": historical,
    }


@app.get("/api/config")
async def get_config():
    """
    获取配置信息。

    返回 LLM 提供商状态、系统配置和环境变量（敏感信息脱敏）。

    返回:
        {
            "llm_providers": { ... },
            "system": { ... },
            "env_vars": [...]
        }
    """
    # LLM 提供商状态
    llm_providers = {
        "openai": {
            "has_key": bool(os.getenv("OPENAI_API_KEY")),
            "model": os.getenv("OPENAI_MODEL", "gpt-4o"),
            "base_url": os.getenv("OPENAI_API_BASE", os.getenv("OPENAI_BASE_URL", "")),
        },
        "anthropic": {
            "has_key": bool(os.getenv("ANTHROPIC_API_KEY")),
            "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        },
        "ollama": {
            "has_key": True,  # Ollama 不需要 API key
            "model": os.getenv("OLLAMA_MODEL", "qwen2.5-coder"),
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        },
        "deepseek": {
            "has_key": bool(os.getenv("DEEPSEEK_API_KEY")),
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        },
    }

    # 系统配置
    system_config = {
        "workspace_dir": str(WORKSPACE_DIR),
        "sandbox_image": os.getenv("SANDBOX_IMAGE", "python:3.10-slim"),
        "sandbox_mem_limit": os.getenv("SANDBOX_MEM_LIMIT", "256m"),
        "sandbox_timeout": int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "60")),
        "max_coder_steps": int(os.getenv("MAX_CODER_STEPS", "15")),
        "max_planner_steps": int(os.getenv("MAX_PLANNER_STEPS", "10")),
        "context_max_tokens": int(os.getenv("CONTEXT_MAX_TOKENS", "8000")),
    }

    # 环境变量列表（敏感信息脱敏）
    env_vars = []
    sensitive_keys = {"key", "secret", "token", "password"}

    for key, value in sorted(os.environ.items()):
        is_sensitive = any(s in key.lower() for s in sensitive_keys)
        env_vars.append({
            "name": key,
            "value": "****" if is_sensitive and value else value,
            "is_sensitive": is_sensitive,
            "is_set": True,
        })

    return {
        "llm_providers": llm_providers,
        "system": system_config,
        "env_vars": env_vars,
    }


@app.get("/api/snapshots")
async def list_snapshots():
    """
    列出所有恢复快照。

    扫描 workspace/.snapshots/ 目录，返回每个快照的元数据。

    返回:
        {
            "snapshots": [
                {
                    "id": "snapshot_name",
                    "timestamp": "2024-01-01T12:00:00",
                    "reason": "max_retries_reached",
                    "active_files_count": 3,
                },
                ...
            ]
        }
    """
    snapshots_dir = os.path.join(WORKSPACE_DIR, ".snapshots")
    snapshots = []

    if not os.path.exists(snapshots_dir):
        return {"snapshots": []}

    try:
        for entry in sorted(os.listdir(snapshots_dir), reverse=True):
            snapshot_path = os.path.join(snapshots_dir, entry)

            if not os.path.isdir(snapshot_path):
                continue

            # 读取元数据
            metadata_path = os.path.join(snapshot_path, "metadata.json")
            metadata = {}

            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                except Exception:
                    pass

            snapshots.append({
                "id": entry,
                "timestamp": metadata.get("timestamp", ""),
                "reason": metadata.get("reason", ""),
                "active_files": metadata.get("active_files", []),
                "active_files_count": len(metadata.get("active_files", [])),
            })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取快照失败: {str(e)}")

    return {"snapshots": snapshots}


@app.get("/api/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    """
    获取指定快照的详细信息。

    返回快照的元数据和包含的代码文件内容。

    参数:
        snapshot_id: 快照目录名称

    返回:
        {
            "metadata": { ... },
            "conversation_summary": "...",
            "code_files": [
                {"path": "...", "content": "..."},
                ...
            ]
        }
    """
    snapshots_dir = os.path.join(WORKSPACE_DIR, ".snapshots")
    snapshot_path = os.path.join(snapshots_dir, snapshot_id)

    if not os.path.exists(snapshot_path):
        raise HTTPException(status_code=404, detail="快照不存在")

    result = {"metadata": {}, "conversation_summary": "", "code_files": []}

    # 读取元数据
    metadata_path = os.path.join(snapshot_path, "metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                result["metadata"] = json.load(f)
        except Exception:
            pass

    # 读取对话摘要
    summary_path = os.path.join(snapshot_path, "conversation_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                result["conversation_summary"] = json.load(f)
        except Exception:
            pass

    # 读取代码文件
    code_dir = os.path.join(snapshot_path, "code")
    if os.path.exists(code_dir):
        for root, dirs, files in os.walk(code_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, code_dir)

                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    result["code_files"].append({
                        "path": relpath,
                        "content": content,
                    })
                except Exception:
                    pass

    return result


@app.get("/api/backups")
async def list_backups():
    """
    列出所有文件备份。

    扫描 workspace/.backups/ 目录，返回每个备份文件的信息。

    返回:
        {
            "backups": [
                {"name": "filename.py.bak", "size": 1234, "mtime": ...},
                ...
            ]
        }
    """
    backups_dir = os.path.join(WORKSPACE_DIR, ".backups")
    backups = []

    if not os.path.exists(backups_dir):
        return {"backups": []}

    try:
        for entry in os.listdir(backups_dir):
            filepath = os.path.join(backups_dir, entry)

            if not os.path.isfile(filepath):
                continue

            stat = os.stat(filepath)
            backups.append({
                "name": entry,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份失败: {str(e)}")

    # 按修改时间倒序排列
    backups.sort(key=lambda b: b["mtime"], reverse=True)

    return {"backups": backups}


@app.get("/api/backups/{backup_name}")
async def read_backup(backup_name: str):
    """
    读取指定备份文件的内容。

    参数:
        backup_name: 备份文件名

    返回:
        {
            "content": "文件内容",
            "size": 1234,
            "mtime": ...
        }
    """
    backups_dir = os.path.join(WORKSPACE_DIR, ".backups")
    filepath = os.path.join(backups_dir, backup_name)

    # 安全检查
    real_path = os.path.realpath(filepath)
    real_root = os.path.realpath(backups_dir)
    if not real_path.startswith(real_root):
        raise HTTPException(status_code=403, detail="禁止访问")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="备份文件不存在")

    try:
        stat = os.stat(filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        return {
            "content": content,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份失败: {str(e)}")


# ============================================================
# 静态文件服务（生产环境）
# ============================================================

def serve_frontend(production: bool = False):
    """
    配置前端静态文件服务。

    在生产模式下，服务 frontend/dist 目录中的构建产物。
    在开发模式下，不挂载静态文件，使用 Vite 开发服务器。

    参数:
        production: 是否为生产模式
    """
    if not production:
        return

    dist_dir = os.path.join(ROOT, "frontend", "dist")

    if os.path.exists(dist_dir):
        # 挂载静态文件
        app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_index(full_path: str):
            # 所有非 API 路由都返回 index.html（SPA 路由）
            index_path = os.path.join(dist_dir, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            raise HTTPException(status_code=404, detail="frontend 未构建")


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  nanoCursor API Server")
    print("=" * 60)
    print(f"  工作区: {WORKSPACE_DIR}")
    print(f"  开发模式: 运行 'cd frontend && npm run dev'")
    print(f"  生产模式: 先 'npm run build'，再运行此脚本")
    print("=" * 60)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8100)
