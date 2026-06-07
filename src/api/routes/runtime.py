"""Runtime integration status routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from src.api.services.go_filetools_service import get_go_filetools_status
from src.api.services.go_indexer_service import get_go_indexer_status
from src.api.services.go_executor_service import get_go_executor_status
from src.api.services.go_mcp_gateway_service import get_go_mcp_gateway_status

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/filetools/status")
async def go_filetools_status():
    return await asyncio.to_thread(get_go_filetools_status)


@router.get("/indexer/status")
async def go_indexer_status():
    return await asyncio.to_thread(get_go_indexer_status)


@router.get("/executor/status")
async def go_executor_status():
    return await asyncio.to_thread(get_go_executor_status)


@router.get("/mcp-gateway/status")
async def go_mcp_gateway_status():
    return await asyncio.to_thread(get_go_mcp_gateway_status)


@router.get("/mcp/status")
async def go_mcp_status():
    return await asyncio.to_thread(get_go_mcp_gateway_status)
