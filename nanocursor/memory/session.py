from __future__ import annotations

import json
import os
import random
import string
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import IO, Any
from io import UnsupportedOperation

from nanocursor.conversation import ConversationManager, Message, ThinkingBlock, ToolResultBlock, ToolUseBlock
from nanocursor.storage import atomic_write

SESSIONS_DIR = ".nanocursor/sessions"
DEFAULT_MAX_AGE_DAYS = 30
TITLE_MAX_LENGTH = 50

SESSION_SUMMARY_PROMPT = (
    "你是一个对话摘要助手。请根据下面的对话内容，用一句话总结这个会话的主要内容。"
    "只输出摘要文本，不要加任何前缀或标点符号外的修饰。不要调用任何工具。"
)


# ---------------------------------------------------------------------------
# RecordType & SessionRecord
# ---------------------------------------------------------------------------


class RecordType(str, Enum):
    HISTORY_BOUNDARY = "history_boundary"
    APPROVAL_CONTEXT = "approval_context"
    SYSTEM_PROMPT = "system_prompt"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"
    COMPRESSION = "compression"
    # Layer-2 compact 标记。auto_compact 压缩对话记录时写入。
    # 内容为结构化载荷（参见 make_compact_boundary / parse_compact_boundary），
    # 包含摘要文本和原样保留的 keep 尾部（以序列化 record 形式内联），
    # 使 resume 可以仅凭此标记重建压缩后的状态，无需重放标记之前的原始前缀。
    COMPACT_BOUNDARY = "compact_boundary"


@dataclass
class SessionRecord:
    type: RecordType
    content: Any
    timestamp: datetime
    tool_use_id: str | None = None
    is_error: bool = False
    memory_context: dict | None = None
    record_id: str | None = None
    generation: int | None = None
    run_id: str | None = None
    model_turn_id: str | None = None
    recovery_source: dict | None = None
    thinking_blocks: list[dict] | None = None

    def to_jsonl(self) -> str:
        data: dict[str, Any] = {
            "type": self.type.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.tool_use_id is not None:
            data["tool_use_id"] = self.tool_use_id
        if self.type == RecordType.TOOL_RESULT:
            data["is_error"] = self.is_error
        if self.memory_context is not None:
            data["memory_context"] = self.memory_context
        for key in ("record_id", "generation", "run_id", "model_turn_id", "recovery_source", "thinking_blocks"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return json.dumps(data, ensure_ascii=False)


    @classmethod
    def from_jsonl(cls, line: str) -> SessionRecord | None:
        try:
            data = json.loads(line)
            if not isinstance(data, dict) or data.get("schema_version", 1) != 1:
                return None
            return cls(
                type=RecordType(data["type"]),
                content=data["content"],
                timestamp=datetime.fromisoformat(data["timestamp"]),
                tool_use_id=data.get("tool_use_id"),
                is_error=data.get("is_error", False),
                memory_context=data.get("memory_context") if isinstance(data.get("memory_context"), dict) else None,
                record_id=data.get("record_id"), generation=data.get("generation"),
                run_id=data.get("run_id"), model_turn_id=data.get("model_turn_id"),
                recovery_source=data.get("recovery_source"),
                thinking_blocks=data.get("thinking_blocks"),
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    @classmethod
    def from_message(cls, message: Message) -> list[SessionRecord]:
        now = datetime.now(timezone.utc)
        records: list[SessionRecord] = []

        if message.tool_results:
            for tr in message.tool_results:
                records.append(
                    cls(
                        type=RecordType.TOOL_RESULT,
                        content=tr.content,
                        timestamp=now,
                        tool_use_id=tr.tool_use_id,
                        is_error=tr.is_error,
                    )
                )
        elif message.role == "assistant":
            if message.tool_uses:
                content_blocks: list[dict[str, Any]] = []
                if message.content:
                    content_blocks.append({"type": "text", "text": message.content})
                for tu in message.tool_uses:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tu.tool_use_id,
                            "name": tu.tool_name,
                            "input": tu.arguments,
                        }
                    )
                records.append(
                    cls(type=RecordType.ASSISTANT, content=content_blocks, timestamp=now)
                )
            else:
                records.append(
                    cls(type=RecordType.ASSISTANT, content=message.content, timestamp=now)
                )
        else:
            records.append(
                cls(type=RecordType.USER, content=message.content, timestamp=now,
                    memory_context=deepcopy(message.memory_context))
            )

        if message.role == "assistant" and message.thinking_blocks:
            for record in records:
                record.thinking_blocks = [asdict(block) for block in message.thinking_blocks]
        return records


# ---------------------------------------------------------------------------
# Compact boundary 载荷（摘要 + 内联的 keep 尾部）
# ---------------------------------------------------------------------------


def _message_to_record_dicts(message: Message) -> list[dict[str, Any]]:
    """将单条 Message 序列化为与磁盘存储格式一致的 record-dict 列表。

    复用 SessionRecord.from_message，使内联的 keep 尾部与正常追加消息的持久化
    结果逐字节一致（assistant 的 tool_uses 变为 content-blocks 列表，每个
    tool_result 独立成一条 record）。这保证了 tool_use↔tool_result 配对的
    无损往返——不像纯 role+content 文本导出那样会丢失 tool call 的关联关系。
    """
    dicts: list[dict[str, Any]] = []
    for rec in SessionRecord.from_message(message):
        data: dict[str, Any] = {"type": rec.type.value, "content": rec.content}
        if rec.tool_use_id is not None:
            data["tool_use_id"] = rec.tool_use_id
        if rec.type == RecordType.TOOL_RESULT:
            data["is_error"] = rec.is_error
        if rec.memory_context is not None:
            data["memory_context"] = rec.memory_context
        dicts.append(data)
    return dicts


def make_compact_boundary(summary: str, keep: list[Message], *, messages: list[Message] | None = None) -> SessionRecord:
    """构建一条 COMPACT_BOUNDARY record，内联摘要和原样保留的 keep 尾部。

    `keep` 是 auto_compact 原样保留的近期尾部消息。将其存储在 boundary record
    内部（而不是依赖它在文件中的物理位置），意味着 resume 可以仅凭 boundary
    重建压缩后的状态——boundary 之前的原始前缀保留在磁盘上但不会被重放。
    """
    keep_dicts: list[dict[str, Any]] = []
    for msg in keep:
        keep_dicts.extend(_message_to_record_dicts(msg))
    payload = {"summary": summary, "keep": keep_dicts}
    if messages is not None:
        payload["history"] = [asdict(m) for m in messages]
    return SessionRecord(
        type=RecordType.COMPACT_BOUNDARY,
        content=payload,
        timestamp=datetime.now(timezone.utc),
    )


def make_history_boundary(messages: list[Message]) -> SessionRecord:
    """An exact, append-only conversation reset after an explicit rewind."""
    return SessionRecord(RecordType.HISTORY_BOUNDARY, [asdict(m) for m in messages],
                         datetime.now(timezone.utc))


def parse_history_boundary(record: SessionRecord) -> list[Message]:
    return [Message(
        role=item["role"], content=item["content"],
        tool_uses=[ToolUseBlock(**b) for b in item.get("tool_uses", [])],
        tool_results=[ToolResultBlock(**b) for b in item.get("tool_results", [])],
        thinking_blocks=[ThinkingBlock(**b) for b in item.get("thinking_blocks", [])],
        memory_context=deepcopy(item.get("memory_context")) if isinstance(item.get("memory_context"), dict) else None,
    ) for item in record.content]


def parse_compact_boundary(record: SessionRecord) -> tuple[str, list[Message]]:
    """make_compact_boundary 的逆操作：返回 (summary, keep_messages)。

    对遗留或格式异常的 payload 降级返回 ("", [])，确保单条损坏的 boundary
    不会导致 resume 崩溃。
    """
    content = record.content
    if not isinstance(content, dict):
        return "", []
    summary = content.get("summary", "")
    keep_raw = content.get("keep", [])
    keep_records: list[SessionRecord] = []
    for item in keep_raw if isinstance(keep_raw, list) else []:
        if not isinstance(item, dict) or "type" not in item:
            continue
        try:
            keep_records.append(
                SessionRecord(
                    type=RecordType(item["type"]),
                    content=item.get("content"),
                    timestamp=record.timestamp,
                    tool_use_id=item.get("tool_use_id"),
                    is_error=item.get("is_error", False),
                    memory_context=item.get("memory_context") if isinstance(item.get("memory_context"), dict) else None,
                )
            )
        except ValueError:
            continue
    return summary, records_to_messages(keep_records)


# ---------------------------------------------------------------------------
# Record ↔ Message 转换
# ---------------------------------------------------------------------------


def records_to_messages(records: list[SessionRecord]) -> list[Message]:
    messages: list[Message] = []
    pending_tool_results: list[ToolResultBlock] = []

    for record in records:
        if record.type == RecordType.APPROVAL_CONTEXT:
            continue  # Metadata is never model conversation or a tool result boundary.
        if record.type == RecordType.TOOL_RESULT:
            pending_tool_results.append(
                ToolResultBlock(
                    tool_use_id=record.tool_use_id or "",
                    content=(
                        record.content
                        if isinstance(record.content, str)
                        else json.dumps(record.content)
                    ),
                    is_error=record.is_error,
                )
            )
            continue

        if pending_tool_results:
            messages.append(
                Message(role="user", content="", tool_results=pending_tool_results)
            )
            pending_tool_results = []

        if record.type == RecordType.HISTORY_BOUNDARY:
            messages = parse_history_boundary(record)
            continue

        if record.type == RecordType.SYSTEM_PROMPT:
            continue

        if record.type == RecordType.COMPRESSION:
            messages.append(
                Message(
                    role="user",
                    content="本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\n" + (record.content or ""),
                )
            )
            continue

        if record.type == RecordType.COMPACT_BOUNDARY:
            if isinstance(record.content, dict) and isinstance(record.content.get("history"), list):
                messages = parse_history_boundary(SessionRecord(
                    RecordType.HISTORY_BOUNDARY, record.content["history"], record.timestamp))
                continue
            # 内联展开：摘要作为 user 消息，后接原样保留的 keep 尾部。
            # resume() 通常已预裁剪到最后一个 boundary，所以这里只会处理
            # 权威的那一条；但在此展开可以保证 records_to_messages 对任何
            # 直接调用者都保持自洽。
            summary, keep_messages = parse_compact_boundary(record)
            messages.append(Message(role="user", content="本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\n" + summary))
            messages.extend(keep_messages)
            continue

        if record.type == RecordType.USER:
            messages.append(Message(role="user", content=record.content or "",
                                    memory_context=deepcopy(record.memory_context)))
        elif record.type == RecordType.ASSISTANT:
            if isinstance(record.content, list):
                text = ""
                tool_uses: list[ToolUseBlock] = []
                for block in record.content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text += block.get("text", "")
                    elif block.get("type") == "tool_use":
                        tool_uses.append(
                            ToolUseBlock(
                                tool_use_id=block.get("id", ""),
                                tool_name=block.get("name", ""),
                                arguments=block.get("input", {}),
                            )
                        )
                messages.append(
                    Message(role="assistant", content=text, tool_uses=tool_uses, thinking_blocks=[ThinkingBlock(**block) for block in (record.thinking_blocks or [])])
                )
            else:
                messages.append(
                    Message(role="assistant", content=record.content or "", thinking_blocks=[ThinkingBlock(**block) for block in (record.thinking_blocks or [])])
                )

    if pending_tool_results:
        messages.append(
            Message(role="user", content="", tool_results=pending_tool_results)
        )

    return messages


# ---------------------------------------------------------------------------
# 消息链校验
# ---------------------------------------------------------------------------


def validate_message_chain(records: list[SessionRecord]) -> int:
    last_valid = 0
    pending_tool_uses: set[str] = set()

    for i, record in enumerate(records):
        if record.type == RecordType.ASSISTANT and isinstance(record.content, list):
            for block in record.content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = block.get("id", "")
                    if tool_id:
                        pending_tool_uses.add(tool_id)

        if record.type == RecordType.TOOL_RESULT and record.tool_use_id:
            pending_tool_uses.discard(record.tool_use_id)

        if not pending_tool_uses:
            last_valid = i + 1

    return last_valid


# ---------------------------------------------------------------------------
# SessionMeta
# ---------------------------------------------------------------------------


@dataclass
class SessionMeta:
    id: str
    title: str = ""
    summary: str = ""
    message_count: int = 0
    total_tokens: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def save(self, path: Path) -> None:
        data = {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
        }
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    @classmethod
    def load(cls, path: Path) -> SessionMeta | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                id=data["id"],
                title=data.get("title", ""),
                summary=data.get("summary", ""),
                message_count=data.get("message_count", 0),
                total_tokens=data.get("total_tokens", 0),
                created_at=datetime.fromisoformat(data["created_at"]),
                last_active=datetime.fromisoformat(data["last_active"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Session（活跃会话句柄）
# ---------------------------------------------------------------------------


class SessionMetadataError(OSError):
    """The JSONL append was flushed, but saving its metadata failed.

    The caller must not replay the already committed message or boundary. The
    in-memory metadata remains updated; a later save or resume can repair it.
    """


class Session:
    def __init__(
        self,
        session_id: str,
        file: IO[str],
        meta: SessionMeta,
        sessions_dir: Path,
    ) -> None:
        self.session_id = session_id
        self._file = file
        self.meta = meta
        self._sessions_dir = sessions_dir
        self._recovery = None
        self._generation = 0

    def save_approval_context(self, context) -> None:
        self.append_record(SessionRecord(RecordType.APPROVAL_CONTEXT, context.to_dict(),
                                         datetime.now(timezone.utc)))

    def load_approval_context(self):
        from nanocursor.permissions.approval_context import AuthorizationContext

        self._file.flush()
        path = self._sessions_dir / f"{self.session_id}.jsonl"
        context = AuthorizationContext()
        seen_content = False
        seen_context = False
        corrupt = False
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    record = SessionRecord.from_jsonl(line)
                    if record is None:
                        corrupt = True
                    elif record.type == RecordType.APPROVAL_CONTEXT:
                        context = AuthorizationContext.from_dict(record.content)
                        seen_context = True
                    elif record.type == RecordType.HISTORY_BOUNDARY:
                        # A crash may land after the rewind record but before
                        # the UI publishes its cleared authorization snapshot.
                        context = AuthorizationContext(complete=False)
                        seen_context = False
                        seen_content = True
                    else:
                        seen_content = True
        except (OSError, UnicodeError):
            corrupt = True
        if corrupt or (seen_content and not seen_context):
            context.complete = False
        return context

    def append(self, message: Message) -> None:
        if self._recovery is not None and self._recovery.session is not self:
            with self._recovery.session_binding(self):
                return self.append(message)
        records = SessionRecord.from_message(message)
        if self._recovery is not None:
            # Persisted identity travels with the object through the host and UI;
            # distinct identical messages are deliberately not deduplicated.
            identities = getattr(message, "_recovery_record_ids", {})
            session_key = self._recovery.session_key
            stored = identities.get(session_key)
            if stored is not None:
                return
            self._append_durable(records)
            if message.tool_results:
                for result, record in zip(message.tool_results, records):
                    result.content = record.content
                    result.is_error = record.is_error
            identities[session_key] = [record.record_id for record in records]
            setattr(message, "_recovery_record_ids", identities)
        else:
            for record in records:
                self._file.write(record.to_jsonl() + "\n")
            self._file.flush()
            try:
                os.fsync(self._file.fileno())
            except (AttributeError, UnsupportedOperation):
                pass  # Explicit in-memory test handles have no durable descriptor.

        self.meta.message_count += 1
        self.meta.last_active = datetime.now(timezone.utc)
        if not self.meta.title and message.role == "user" and message.content:
            self.meta.title = message.content[:TITLE_MAX_LENGTH]
        self._save_metadata_after_append()

    def _append_durable(self, records: list[SessionRecord]) -> None:
        try:
            with self._recovery.session_binding(self):
                self._append_durable_bound(records)
        except OSError as exc:
            from nanocursor.recovery import RecoveryStorageError
            self._recovery.store.failed = True
            raise RecoveryStorageError("Session projection failed; execution is stopped") from exc

    def _append_durable_bound(self, records: list[SessionRecord]) -> None:
        from nanocursor.recovery.store import identifier
        runtime = self._recovery
        for record in records:
            payload = json.loads(record.to_jsonl())
            if record.type == RecordType.TOOL_RESULT and record.tool_use_id:
                completed = runtime.tool_record(record.tool_use_id)
                if completed is not None:
                    # The model-visible view may be deliberately truncated. The
                    # complete observed value remains in operations.result.
                    if completed["recovery_source"]["state"] == "outcome_unknown":
                        marker = "[Host recovery observation: the external outcome is unknown. This is not a confirmed tool result; do not replay automatically.]"
                        if not str(record.content).startswith(marker):
                            record.content = marker + "\n" + str(record.content)
                        record.is_error = True
                    completed["content"] = record.content
                    completed["is_error"] = record.is_error
                    payload = completed
            payload.setdefault("record_id", identifier("record"))
            if record.recovery_source is None:
                if runtime.run_id is not None:
                    payload.setdefault("run_id", runtime.run_id)
                if runtime.model_turn_id is not None:
                    payload.setdefault("model_turn_id", runtime.model_turn_id)
            saved = runtime.store.enqueue_record(runtime.session_key, payload,
                new_generation=record.type == RecordType.HISTORY_BOUNDARY)
            record.record_id = saved["record_id"]
            record.generation = saved["generation"]
            runtime.generation = saved["generation"]
            self._generation = saved["generation"]
        path = self._sessions_dir / f"{self.session_id}.jsonl"
        self._file.flush()
        runtime.store.project(runtime.session_key, path)
        self._file.close()
        self._file = path.open("a", encoding="utf-8")

    def append_record(self, record: SessionRecord) -> None:
        """Persist structural records through the same complete-payload outbox."""
        if self._recovery is not None:
            self._append_durable([record])
        else:
            self._file.write(record.to_jsonl() + "\n")
            self._file.flush()
            try:
                os.fsync(self._file.fileno())
            except (AttributeError, UnsupportedOperation):
                pass
        self.meta.last_active = datetime.now(timezone.utc)
        self._save_metadata_after_append()

    def _save_metadata_after_append(self) -> None:
        try:
            self.meta.save(self._sessions_dir / f"{self.session_id}.meta")
        except Exception as exc:
            raise SessionMetadataError(
                f"Session records were saved, but metadata could not be updated: {exc}"
            ) from exc

    def reset_history(self, messages: list[Message], *, record_id: str | None = None) -> None:
        boundary = make_history_boundary(messages)
        boundary.record_id = record_id
        if record_id is not None and self._recovery is not None:
            old = self._recovery.store.rows("SELECT payload FROM outbox WHERE record_id=?", (record_id,))
            if old:
                boundary = SessionRecord.from_jsonl(old[0]["payload"])
        try:
            self.append_record(boundary)
        except SessionMetadataError:
            self.meta.message_count = len(messages)
            raise
        self.meta.message_count = len(messages)
        self._save_metadata_after_append()


    def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.flush()
            self._file.close()


# ---------------------------------------------------------------------------
# ResumeResult
# ---------------------------------------------------------------------------


@dataclass
class ResumeResult:
    session: Session
    messages: list[Message]
    last_active: datetime


# ---------------------------------------------------------------------------
# Session 摘要生成
# ---------------------------------------------------------------------------


async def generate_session_summary(
    client: Any, conversation: ConversationManager, protocol: str
) -> str:
    from nanocursor.client import collect_text_response

    recent = deepcopy(conversation.history[-10:])
    if not recent:
        return ""

    summary_conv = ConversationManager()
    summary_conv.history = [Message(role="user", content=SESSION_SUMMARY_PROMPT)]
    for msg in recent:
        summary_conv.history.append(msg)
    summary_conv.history.append(
        Message(role="user", content="请用一句话总结上面的对话内容。不要调用工具。")
    )

    try:
        response = await collect_text_response(client, summary_conv, system=SESSION_SUMMARY_PROMPT)
    except Exception:
        return ""

    return response.text


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


def _generate_session_id() -> str:
    now = datetime.now()
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"session_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


class SessionManager:
    def __init__(self, work_dir: str, *, recovery=None) -> None:
        self._recovery = recovery
        self._sessions_dir = Path(work_dir) / SESSIONS_DIR
        self._sessions_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if recovery is not None:
            from nanocursor.recovery.store import sync_directory
            sync_directory(self._sessions_dir.parent)
            sync_directory(self._sessions_dir.parent.parent)


    @property
    def recovery_runtime(self):
        return self._recovery

    @recovery_runtime.setter
    def recovery_runtime(self, runtime):
        self._recovery = runtime

    def create(self) -> Session:
        session_id = _generate_session_id()
        jsonl_path = self._sessions_dir / f"{session_id}.jsonl"
        meta = SessionMeta(id=session_id)
        meta.save(self._sessions_dir / f"{session_id}.meta")

        fd = os.open(jsonl_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        file = os.fdopen(fd, "a", encoding="utf-8")
        if self._recovery is not None:
            from nanocursor.recovery.store import sync_directory
            sync_directory(self._sessions_dir)
            sync_directory(self._sessions_dir.parent)
            sync_directory(self._sessions_dir.parent.parent)
        session = Session(
            session_id=session_id, file=file, meta=meta, sessions_dir=self._sessions_dir,
        )
        if self._recovery is not None:
            self._recovery.bind_session(session)
        return session


    def list(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for meta_path in self._sessions_dir.glob("*.meta"):
            meta = SessionMeta.load(meta_path)
            if meta is not None:
                metas.append(meta)
        metas.sort(key=lambda m: m.last_active, reverse=True)
        return metas

    def resume(self, session_id: str) -> ResumeResult | None:
        jsonl_path = self._sessions_dir / f"{session_id}.jsonl"
        meta_path = self._sessions_dir / f"{session_id}.meta"

        if not jsonl_path.exists():
            return None

        meta = SessionMeta.load(meta_path)
        if meta is None:
            return None

        if self._recovery is not None:
            temporary = Session(session_id, jsonl_path.open("a", encoding="utf-8"), meta, self._sessions_dir)
            try:
                with self._recovery.session_binding(temporary):
                    self._recovery.reconcile_session()
            finally:
                temporary.close()

        raw_records: list[SessionRecord | None] = []
        last_line = b""
        with open(jsonl_path, "rb") as f:
            for line in f:
                last_line = line
                if not line.strip():
                    continue
                try:
                    record = SessionRecord.from_jsonl(line.decode("utf-8"))
                except UnicodeError:
                    record = None
                # Keep corruption in the sequence: replaying records after an
                # unknown gap would invent a history whose protocol is unknown.
                raw_records.append(record)

        # 重建压缩后的状态：仅从最后一个 compact_boundary 开始重放。
        # 该标记之前的 record 是已被摘要过的原始前缀——保留在磁盘上供审计，
        # 但不再重放。标记本身内联了摘要 + 原样 keep 尾部，标记之后追加的
        # 普通消息（续写）照常重放。没有 boundary 则全量重放（兼容旧 session）。
        last_boundary = -1
        for i, rec in enumerate(raw_records):
            if rec is not None and rec.type in (RecordType.COMPACT_BOUNDARY, RecordType.HISTORY_BOUNDARY):
                last_boundary = i
        if last_boundary >= 0:
            raw_records = raw_records[last_boundary:]

        needs_recovery = None in raw_records
        records: list[SessionRecord] = []
        for record in raw_records:
            if record is None:
                break
            records.append(record)

        valid_count = validate_message_chain(records)
        if self._recovery is not None and valid_count != len(records) and any(record.record_id for record in records[valid_count:]):
            from nanocursor.recovery import RecoveryRequired
            raise RecoveryRequired(message="This protected session still has an unfinished tool batch; inspect its live execution before resuming")
        needs_recovery = needs_recovery or valid_count != len(records)
        records = records[:valid_count]
        messages = records_to_messages(records)

        last_active = meta.last_active
        file = open(jsonl_path, "a", encoding="utf-8")  # noqa: SIM115
        session = Session(
            session_id=session_id,
            file=file,
            meta=meta,
            sessions_dir=self._sessions_dir,
        )

        try:
            if last_line and not last_line.endswith(b"\n"):
                file.write("\n")
                file.flush()
            if needs_recovery:
                # Preserve all original bytes while establishing the only
                # history future appends and resumes should replay.
                session.reset_history(messages)
                os.fsync(file.fileno())
            elif meta.message_count != len(messages):
                # JSONL is authoritative even if a crash preceded meta.save().
                meta.message_count = len(messages)
                meta.save(meta_path)
        except BaseException:
            session.close()
            raise

        if self._recovery is not None:
            session._recovery = self._recovery
            if self._recovery.session is None or self._recovery.session._file.closed:
                self._recovery.bind_session(session)
        return ResumeResult(
            session=session,
            messages=messages,
            last_active=last_active,
        )

    def _has_recovery_evidence(self, session_id: str) -> bool:
        if self._recovery is None:
            return False
        store = self._recovery.store
        if store.rows("SELECT 1 FROM operations WHERE session_id=? LIMIT 1", (session_id,)):
            return True
        tables = {row["name"] for row in store.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("checkpoint_records", "file_edit_records", "file_restores"):
            if table in tables and store.rows(f"SELECT 1 FROM {table} WHERE session_id=? LIMIT 1", (session_id,)):
                return True
        return False

    def delete(self, session_id: str) -> bool:
        if self._has_recovery_evidence(session_id):
            return False
        jsonl_path = self._sessions_dir / f"{session_id}.jsonl"
        meta_path = self._sessions_dir / f"{session_id}.meta"

        deleted = False
        if jsonl_path.exists():
            jsonl_path.unlink()
            deleted = True
        if meta_path.exists():
            meta_path.unlink()
            deleted = True
        return deleted

    def cleanup(self, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0

        for meta_path in list(self._sessions_dir.glob("*.meta")):
            meta = SessionMeta.load(meta_path)
            if meta is not None and meta.last_active < cutoff:
                if self.delete(meta.id):
                    removed += 1

        return removed
