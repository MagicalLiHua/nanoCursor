"""gRPC client for go-eventstore service."""

import json
import os
import threading

import grpc

from src.eventstore.proto import eventstore_pb2 as pb
from src.eventstore.proto import eventstore_pb2_grpc as pb_grpc

EVENTSTORE_ADDR = os.getenv("NANOCURSOR_EVENTSTORE_ADDR", "localhost:50058")

_channel = None
_stub = None


def _ensure_channel():
    global _channel, _stub
    if _channel is None:
        _channel = grpc.insecure_channel(EVENTSTORE_ADDR)
        _stub = pb_grpc.EventStoreServiceStub(_channel)
    return _stub


def close():
    global _channel, _stub
    if _channel is not None:
        _channel.close()
        _channel = None
        _stub = None


def health():
    stub = _ensure_channel()
    resp = stub.Health(pb.HealthRequest(), timeout=5)
    return {"ok": resp.ok, "service": resp.service, "version": resp.version}


def create_session(thread_id, prompt, workspace_dir, status="", mode=""):
    stub = _ensure_channel()
    resp = stub.CreateSession(pb.CreateSessionRequest(
        thread_id=thread_id, prompt=prompt, workspace_dir=workspace_dir,
        status=status, mode=mode,
    ), timeout=5)
    return _session_to_dict(resp)


def get_session(thread_id, workspace_dir=""):
    stub = _ensure_channel()
    resp = stub.GetSession(pb.GetSessionRequest(
        thread_id=thread_id, workspace_dir=workspace_dir,
    ), timeout=5)
    if not resp.thread_id:
        return None
    return _session_to_dict(resp)


def update_session(thread_id, workspace_dir="", **changes):
    stub = _ensure_channel()
    resp = stub.UpdateSession(pb.UpdateSessionRequest(
        thread_id=thread_id, workspace_dir=workspace_dir,
        changes=changes,
    ), timeout=5)
    if not resp.thread_id:
        return None
    return _session_to_dict(resp)


def append_event(thread_id, event_type, title="", content="", agent="lead",
                  payload=None, workspace_dir=""):
    stub = _ensure_channel()
    resp = stub.AppendEvent(pb.AppendEventRequest(
        thread_id=thread_id, event_type=event_type, title=title,
        content=content, agent=agent,
        payload_json=json.dumps(payload or {}),
        workspace_dir=workspace_dir,
    ), timeout=5)
    return _event_to_dict(resp)


def list_events(thread_id, workspace_dir="", after=0):
    stub = _ensure_channel()
    resp = stub.ListEvents(pb.ListEventsRequest(
        thread_id=thread_id, workspace_dir=workspace_dir, after=after,
    ), timeout=10)
    return [_event_to_dict(e) for e in resp.events]


def count_events(thread_id, workspace_dir=""):
    stub = _ensure_channel()
    resp = stub.CountEvents(pb.CountEventsRequest(
        thread_id=thread_id, workspace_dir=workspace_dir,
    ), timeout=5)
    return resp.count


def workspace_for_thread(thread_id):
    stub = _ensure_channel()
    resp = stub.WorkspaceForThread(pb.WorkspaceForThreadRequest(
        thread_id=thread_id,
    ), timeout=5)
    if resp.found:
        return resp.workspace_dir
    return None


def subscribe_events(thread_id, callback, workspace_dir=""):
    """Blocking: streams events, calling callback for each."""
    stub = _ensure_channel()
    stream = stub.SubscribeEvents(pb.SubscribeEventsRequest(
        thread_id=thread_id, workspace_dir=workspace_dir,
    ))
    try:
        for event in stream:
            callback(_event_to_dict(event))
    except grpc.RpcError:
        pass


def subscribe_events_async(thread_id, callback, workspace_dir=""):
    """Non-blocking: runs subscribe in a background thread."""
    t = threading.Thread(target=subscribe_events, args=(thread_id, callback, workspace_dir), daemon=True)
    t.start()
    return t


def _session_to_dict(s):
    return {
        "thread_id": s.thread_id, "workspace_dir": s.workspace_dir,
        "status": s.status, "prompt": s.prompt, "mode": s.mode,
        "created_at": s.created_at, "updated_at": s.updated_at,
    }


def _event_to_dict(e):
    return {
        "id": e.id, "thread_id": e.thread_id, "type": e.type,
        "timestamp": e.timestamp, "agent": e.agent, "title": e.title,
        "content": e.content, "payload": e.payload_json,
    }
