"""Tests for LLM retry logic."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.engine import _is_retryable_error, _retryable_llm_call, backoff_delay


def test_backoff_delay_increases():
    d0 = backoff_delay(0)
    d1 = backoff_delay(1)
    d2 = backoff_delay(2)
    assert d0 < d1 < d2
    assert d2 <= 30.0


def test_is_retryable_error():
    from anthropic import RateLimitError, InternalServerError, APIConnectionError, APITimeoutError, BadRequestError, AuthenticationError

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {}
    assert _is_retryable_error(RateLimitError(message="rate limited", response=mock_resp, body=None))

    mock_resp500 = MagicMock()
    mock_resp500.status_code = 500
    mock_resp500.headers = {}
    assert _is_retryable_error(InternalServerError(message="server error", response=mock_resp500, body=None))

    assert _is_retryable_error(APIConnectionError(request=MagicMock()))

    assert _is_retryable_error(APITimeoutError(request=MagicMock()))

    mock_resp400 = MagicMock()
    mock_resp400.status_code = 400
    mock_resp400.headers = {}
    assert not _is_retryable_error(BadRequestError(message="bad request", response=mock_resp400, body=None))

    mock_resp401 = MagicMock()
    mock_resp401.status_code = 401
    mock_resp401.headers = {}
    assert not _is_retryable_error(AuthenticationError(message="auth error", response=mock_resp401, body=None))


def test_retryable_llm_call_succeeds_first_try():
    async def run():
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value="success")
        result = await _retryable_llm_call(mock_client, model="test", messages=[])
        assert result == "success"
        assert mock_client.messages.create.call_count == 1
    asyncio.run(run())


def test_retryable_llm_call_retries_on_rate_limit():
    from anthropic import RateLimitError

    async def run():
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}

        call_count = 0
        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RateLimitError(message="rate limited", response=mock_resp, body=None)
            return "success"

        mock_client.messages.create = mock_create
        result = await _retryable_llm_call(mock_client, model="test", messages=[])
        assert result == "success"
        assert call_count == 3
    asyncio.run(run())


def test_retryable_llm_call_raises_after_max_retries():
    from anthropic import RateLimitError

    async def run():
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}

        async def mock_create(**kwargs):
            raise RateLimitError(message="rate limited", response=mock_resp, body=None)

        mock_client.messages.create = mock_create
        with pytest.raises(RateLimitError):
            await _retryable_llm_call(mock_client, model="test", messages=[])
    asyncio.run(run())


def test_retryable_llm_call_no_retry_on_bad_request():
    from anthropic import BadRequestError

    async def run():
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.headers = {}

        call_count = 0
        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            raise BadRequestError(message="bad request", response=mock_resp, body=None)

        mock_client.messages.create = mock_create
        with pytest.raises(BadRequestError):
            await _retryable_llm_call(mock_client, model="test", messages=[])
        assert call_count == 1
    asyncio.run(run())
