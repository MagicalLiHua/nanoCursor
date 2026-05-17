"""
Project tools - Agents can search and query the project codebase structure.

Uses ProjectIndex to provide structured understanding beyond raw grep.
"""

from pathlib import Path

from src.infra.logger import logger
from src.indexer.indexer import get_project_index


def search_codebase(query: str, search_type: str = "symbol") -> str:
    """
    搜索代码库结构化索引。

    Args:
        query: 搜索词（符号名、模块路径、或关键词）
        search_type: symbol (类/函数), import (导入关系), dependency (依赖者)
    """
    try:
        idx = get_project_index()
        idx.update()  # 增量更新确保索引是最新的

        if search_type == "symbol":
            results = idx.search_symbol(query)
            if not results:
                return f"未找到符号: {query}"
            lines = [f"搜索 '{query}' 找到 {len(results)} 个符号:"]
            for r in results:
                lines.append(
                    f"  [{r['symbol_type']}] {r['symbol_name']} "
                    f"→ {r['file']}:{r['lineno']}"
                )
            return "\n".join(lines)

        elif search_type == "dependency":
            deps = idx.search_dependents(query)
            if not deps:
                return f"没有文件依赖 '{query}'"
            lines = [f"依赖 '{query}' 的文件 ({len(deps)}):"]
            for d in sorted(deps):
                lines.append(f"  - {d}")
            return "\n".join(lines)

        elif search_type == "import":
            # 搜索导入该模块的文件
            deps = idx.search_dependents(query)
            if not deps:
                return f"没有文件导入 '{query}'"
            lines = [f"导入 '{query}' 的文件 ({len(deps)}):"]
            for d in sorted(deps):
                lines.append(f"  - {d}")
            return "\n".join(lines)

        else:
            return f"未知搜索类型: {search_type}。可用: symbol, import, dependency"

    except Exception as e:
        logger.error(f"search_codebase failed: {e}")
        return f"搜索代码库失败: {e}"


def project_context() -> str:
    """返回当前项目的结构化上下文，帮助 Agent 快速理解项目结构。"""
    try:
        idx = get_project_index()
        idx.update()

        s = idx.summary()
        lines = [f"=== 项目概况: {idx.workspace.name} ===", ""]

        # 入口点
        if s["entry_points"]:
            lines.append(f"**入口点**: {', '.join(s['entry_points'])}")

        # 统计
        lines.append(
            f"**文件**: {s['total_files']} 个 "
            f"(source: {s['source_count']}, test: {s['test_count']}, config: {s['config_count']})"
        )
        lines.append(f"**代码行数**: {s['total_loc']:,} 行")
        lines.append("")

        # 最近修改
        if s["recently_modified"]:
            lines.append("**最近修改**:")
            for path, _ in s["recently_modified"][:5]:
                lines.append(f"  - {path}")
            lines.append("")

        # 关键模块
        modules = s.get("modules", {})
        if modules:
            lines.append("**关键模块**:")
            for module_path, info in sorted(modules.items()):
                if info.get("role") == "source":
                    syms = [f"{sym['type']} {sym['name']}" for sym in info.get("symbols", [])[:3]]
                    if syms:
                        lines.append(f"  - {module_path}: {', '.join(syms)}")

        # 依赖图摘要
        dep_graph = s.get("dependency_graph", {})
        if dep_graph:
            # Find most imported files
            imported_count = {}
            for file, deps in dep_graph.items():
                for d in deps:
                    imported_count[d] = imported_count.get(d, 0) + 1
            top_imported = sorted(imported_count.items(), key=lambda x: x[1], reverse=True)[:5]
            if top_imported:
                lines.append("")
                lines.append("**被最多依赖的模块**:")
                for mod, count in top_imported:
                    lines.append(f"  - {mod} (被 {count} 个文件导入)")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"project_context failed: {e}")
        return f"获取项目上下文失败: {e}"


# Anthropic-format tool schemas
PROJECT_TOOLS = [
    {
        "name": "search_codebase",
        "description": "Search codebase structure: find symbols (classes/functions), imports, or dependencies",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symbol name, module path, or keyword"},
                "search_type": {
                    "type": "string",
                    "description": "symbol (find class/function defs) | import (find importers) | dependency (find dependents)",
                },
            },
            "required": ["query", "search_type"],
        },
    },
    {
        "name": "project_context",
        "description": "Get structured overview of the current project (entry points, modules, dependencies, recent changes)",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]
