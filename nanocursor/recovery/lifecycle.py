"""Host-owned effect tracking shared by hooks and maintenance jobs."""
from __future__ import annotations

from contextlib import nullcontext
from typing import Awaitable, Callable, TypeVar

from .models import RecoveryError
from .runtime import current_runtime

T = TypeVar('T')


def prepare_effect(kind: str, name: str, arguments=None):
    runtime = current_runtime()
    return (runtime, runtime.begin_operation(kind, name, arguments)) if runtime else (None, None)


async def run_effect(kind: str, name: str, action: Callable[[], Awaitable[T]], arguments=None, *, prepared=None) -> T:
    runtime, operation_id = prepared if prepared is not None else prepare_effect(kind, name, arguments)
    try:
        with runtime.activate() if runtime else nullcontext():
            with runtime.operation_context(operation_id) if runtime else nullcontext():
                result = await action()
        if runtime:
            if getattr(result, 'outcome_unknown', False):
                runtime.mark_unknown(operation_id, str(getattr(result, 'output', result)))
            else:
                output = getattr(result, 'output', None)
                if output is None:
                    output = result if isinstance(result, (str, dict, list, int, bool, type(None))) else str(result)
                runtime.finish_operation(operation_id, output,
                                         is_error=not getattr(result, 'success', True))
        return result
    except RecoveryError as exc:
        if runtime and not runtime.store.failed:
            runtime.mark_unknown(operation_id, f"Execution stopped by recovery protection: {exc}")
        raise
    except BaseException as exc:
        if runtime:
            runtime.mark_unknown(operation_id, f'{type(exc).__name__}: {exc}')
        raise
