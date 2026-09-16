from __future__ import annotations

from typing import Any

from mcp import types as mcp_types
from pydantic import BaseModel, RootModel, model_validator
from jsonschema.validators import validator_for
from referencing import Registry

from nanocursor.mcp.client import MCPClient
from nanocursor.tools.base import Tool, ToolResult


def _build_params_model(
    tool_name: str, input_schema: dict[str, Any]
) -> type[BaseModel]:
    validator_type = validator_for(input_schema)
    validator_type.check_schema(input_schema)
    validator = validator_type(input_schema, registry=Registry(), format_checker=validator_type.FORMAT_CHECKER)

    class Params(RootModel[dict[str, Any]]):
        @model_validator(mode="before")
        @classmethod
        def validate_schema(cls, value: Any) -> Any:
            errors = list(validator.iter_errors(value))
            if errors:
                error = errors[0]
                location = ".".join(map(str, error.absolute_path)) or "$"
                raise ValueError(f"{location}: {error.message}")
            return value

    Params.__name__ = f"{tool_name}Params"
    return Params


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        elif isinstance(block, mcp_types.ImageContent):
            parts.append(f"[image: {block.mimeType}]")
        elif isinstance(block, mcp_types.EmbeddedResource):
            resource = block.resource
            if hasattr(resource, "text"):
                parts.append(resource.text)
            else:
                parts.append(f"[binary resource: {resource.uri}]")
    return "\n".join(parts) if parts else "(no output)"


class MCPToolWrapper(Tool):
    def __init__(
        self,
        server_name: str,
        tool_def: mcp_types.Tool,
        client: MCPClient,
    ) -> None:
        self._server_name = server_name
        self._tool_def = tool_def
        self._client = client
        self.name = f"mcp_{server_name}_{tool_def.name}"
        self.description = tool_def.description or tool_def.name
        self.category = "command"
        self.is_concurrency_safe = False
        self.should_defer = True
        self.params_model = _build_params_model(
            tool_def.name, tool_def.inputSchema
        )

    @property
    def mcp_tool_name(self) -> str:
        return self._tool_def.name


    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._tool_def.inputSchema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        if not self._client.is_alive:
            try:
                await self._client.connect()
            except Exception as e:
                return ToolResult(
                    output=f"MCP server '{self._server_name}' reconnect failed: {e}",
                    is_error=True,
                )

        try:
            result = await self._client.call_tool(
                self._tool_def.name, params.model_dump()
            )
        except Exception as e:
            self._client._alive = False
            return ToolResult(
                output=f"MCP tool call failed: {e}",
                is_error=True,
            )

        text = _extract_text(result.content)
        return ToolResult(output=text, is_error=bool(result.isError))
