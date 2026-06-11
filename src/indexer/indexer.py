"""
ProjectIndex - 项目代码库结构索引，提供结构化理解。

扫描工作区文件，用 AST 解析 Python 源码，构建：
- 文件分类 (entry_point / source / test / config / doc)
- 模块依赖图 (import 关系)
- 符号表 (类/函数定义位置)
- 增量更新 (基于 mtime)
"""

import ast
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from src.infra.logger import logger


@dataclass
class FileEntry:
    path: str           # 相对路径
    role: str           # entry_point / source / test / config / doc / data
    language: str       # python / javascript / typescript / yaml / json / text
    symbols: list[dict] # [{name, type (class/function), lineno}]
    imports: list[str]  # list of imported module paths
    mtime: float
    size: int           # file size in bytes
    loc: int = 0        # actual lines of code (non-blank, non-comment)
    routes: list[dict] = field(default_factory=list)  # [{method, path, handler, lineno}]
    call_graph: dict[str, list[str]] = field(default_factory=dict)  # {function_name: [called_function_names]}


# Python entry point file names
ENTRY_POINT_NAMES = {"cli.py", "run.py", "main.py", "app.py",
                     "__main__.py", "setup.py"}

# Config file extensions
CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env"}
DOC_EXTS = {".md", ".rst", ".txt", ".markdown"}
PY_EXTS = {".py"}


def _classify_file(filepath: Path) -> str:
    """启发式分类文件角色"""
    name = filepath.name.lower()
    parts = filepath.parts

    # Entry point
    if name in ENTRY_POINT_NAMES:
        return "entry_point"

    # Test
    if "test" in parts or "tests" in parts or name.startswith("test_"):
        return "test"

    # Config
    if filepath.suffix.lower() in CONFIG_EXTS:
        return "config"

    # Doc
    if filepath.suffix.lower() in DOC_EXTS:
        return "doc"

    return "source"


def _parse_python_file(filepath: Path) -> tuple[list[dict], list[str], list[dict], dict[str, list[str]]]:
    """用 AST 解析 Python 文件，提取符号、导入、路由和调用图"""
    symbols = []
    imports = []
    routes = []
    call_graph: dict[str, list[str]] = {}

    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return symbols, imports, routes, call_graph

    for node in ast.walk(tree):
        # Class definitions
        if isinstance(node, ast.ClassDef):
            symbols.append({
                "name": node.name,
                "type": "class",
                "lineno": node.lineno,
            })
            # Extract routes from class-based views (Flask MethodView / FastAPI router)
            _extract_class_routes(node, routes)

        # Top-level function definitions
        elif isinstance(node, ast.FunctionDef):
            if _is_top_level(node, tree):
                symbols.append({
                    "name": node.name,
                    "type": "function",
                    "lineno": node.lineno,
                })
            # Extract routes from decorators
            _extract_route_decorators(node, routes)
            # Extract call graph for this function
            callees = _extract_function_calls(node)
            if callees:
                call_graph[node.name] = callees

        # Decorator-based routes on functions
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}" if module else alias.name)

    return symbols, imports, routes, call_graph


def _extract_route_decorators(func_node: ast.FunctionDef, routes: list[dict]) -> None:
    """Extract Flask/FastAPI route decorators from a function."""
    for decorator in func_node.decorator_list:
        route_info = None
        # Flask: @app.route('/path', methods=['GET', 'POST'])
        if isinstance(decorator, ast.Call):
            if _is_route_decorator(decorator.func):
                path = decorator.args[0].value if decorator.args else "/"
                methods = ["GET"]
                for kw in decorator.keywords:
                    if kw.arg == "methods":
                        if isinstance(kw.value, ast.List):
                            methods = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                route_info = {"method": ", ".join(methods), "path": path, "handler": func_node.name, "lineno": func_node.lineno}
        # FastAPI: @app.get('/path') or @router.post('/path')
        elif isinstance(decorator, ast.Attribute):
            method = decorator.attr.upper()  # get -> GET, post -> POST
            if method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                # Walk up to find the call
                pass  # Handled in the Call case below
        if route_info:
            routes.append(route_info)
            return

    # Also check for FastAPI-style: @router.get('/path') where the decorator IS a Call
    for decorator in func_node.decorator_list:
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
            method = decorator.func.attr.upper()
            if method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                path = decorator.args[0].value if decorator.args else "/"
                routes.append({
                    "method": method,
                    "path": path,
                    "handler": func_node.name,
                    "lineno": func_node.lineno,
                })


def _extract_class_routes(class_node: ast.ClassDef, routes: list[dict]) -> None:
    """Extract routes from class-based views and methods with route decorators."""
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef):
            for decorator in item.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    method = decorator.func.attr.upper()
                    if method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                        path = decorator.args[0].value if decorator.args else "/"
                        routes.append({
                            "method": method,
                            "path": path,
                            "handler": f"{class_node.name}.{item.name}",
                            "lineno": item.lineno,
                        })


def _is_route_decorator(func: ast.expr) -> bool:
    """Check if an AST expression is a route decorator like app.route or bp.route."""
    if isinstance(func, ast.Attribute) and func.attr == "route":
        return True
    return False


def _extract_function_calls(func_node: ast.FunctionDef) -> list[str]:
    """Extract names of locally-defined functions called within this function."""
    callees = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                callees.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                callees.append(node.func.attr)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for name in callees:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result[:20]  # Cap at 20 to avoid bloating


def _is_top_level(node: ast.AST, tree: ast.AST) -> bool:
    """检查函数是否在模块顶层"""
    for child in ast.iter_child_nodes(tree):
        if child is node:
            return True
    return False


def _parse_js_like_file(filepath: Path) -> tuple[list[dict], list[str], list[dict], dict[str, list[str]]]:
    """正则解析 JS/TS 文件的导入和路由"""
    imports = []
    symbols = []
    routes = []
    call_graph: dict[str, list[str]] = {}

    try:
        content = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return symbols, imports, routes, call_graph

    # ES6 imports / require
    import_patterns = [
        r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',
        r'require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)',
    ]
    for pat in import_patterns:
        imports.extend(re.findall(pat, content))

    # Top-level function/class declarations
    func_matches = re.findall(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)', content, re.MULTILINE)
    symbols.extend([{"name": m, "type": "function", "lineno": 0} for m in func_matches])
    class_matches = re.findall(r'^(?:export\s+)?class\s+(\w+)', content, re.MULTILINE)
    symbols.extend([{"name": m, "type": "class", "lineno": 0} for m in class_matches])

    # Express-style route extraction
    route_patterns = [
        r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*[\'"]([^\'"]+)[\'"]',
        r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*`([^`]+)`',
    ]
    for pat in route_patterns:
        for m in re.findall(pat, content, re.IGNORECASE):
            routes.append({"method": m[0].upper(), "path": m[1], "handler": "?", "lineno": 0})

    return symbols, imports, routes, call_graph


class ProjectIndex:
    """项目代码库结构索引"""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.index_path = self.workspace / ".nanocursor" / "project_index.json"
        self.entries: dict[str, FileEntry] = {}
        self._ensure_dir()

    def _ensure_dir(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Build / Update ─────────────────────────────────────────────

    def build(self, force: bool = False) -> bool:
        """全量扫描构建索引。返回 True 表示已构建"""
        if not force and self.index_path.exists():
            self._load()
            return False

        logger.info("[Indexer] Building full project index...")
        self.entries.clear()

        for filepath in self._scannable_files():
            entry = self._index_file(filepath)
            if entry:
                rel = str(filepath.relative_to(self.workspace))
                self.entries[rel] = entry

        self._save()
        logger.info(f"[Indexer] Indexed {len(self.entries)} files")
        return True

    def update(self) -> int:
        """增量更新。返回更新的文件数"""
        if not self.index_path.exists():
            self.build()
            return len(self.entries)

        self._load()
        updated = 0

        current_files = {str(f.relative_to(self.workspace)): f
                        for f in self._scannable_files()}

        # 检查新增/修改
        for rel, filepath in current_files.items():
            mtime = filepath.stat().st_mtime
            if rel not in self.entries or self.entries[rel].mtime < mtime:
                entry = self._index_file(filepath)
                if entry:
                    self.entries[rel] = entry
                    updated += 1

        # 检查删除
        removed = [r for r in self.entries if r not in current_files]
        for r in removed:
            del self.entries[r]

        if updated or removed:
            self._save()
            logger.info(f"[Indexer] Updated {updated} files, removed {len(removed)}")
        return updated

    # ── Query ───────────────────────────────────────────────────────

    def search_symbol(self, query: str) -> list[dict]:
        """搜索符号名，返回 [{file, symbol_name, symbol_type, lineno}]"""
        results = []
        query_lower = query.lower()
        for rel, entry in self.entries.items():
            for s in entry.symbols:
                if query_lower in s["name"].lower():
                    results.append({
                        "file": rel,
                        "symbol_name": s["name"],
                        "symbol_type": s["type"],
                        "lineno": s["lineno"],
                    })
        return results[:20]

    def search_dependents(self, module: str) -> list[str]:
        """搜索哪些文件导入了指定模块"""
        dependents = []
        module_base = module.replace("/", ".").replace(".py", "")
        for rel, entry in self.entries.items():
            for imp in entry.imports:
                if module_base in imp:
                    dependents.append(rel)
                    break
        return dependents

    def summary(self) -> dict:
        """返回项目的结构化摘要"""
        if not self.entries:
            self.build()

        entry_points = [p for p, e in self.entries.items() if e.role == "entry_point"]
        sources = [p for p, e in self.entries.items() if e.role == "source"]
        tests = [p for p, e in self.entries.items() if e.role == "test"]
        configs = [p for p, e in self.entries.items() if e.role == "config"]
        total_loc = sum(e.loc for e in self.entries.values())

        modules = {}
        for rel, entry in self.entries.items():
            if entry.symbols:
                modules[rel] = {
                    "role": entry.role,
                    "symbols": entry.symbols,
                }

        dep_graph = {rel: entry.imports for rel, entry in self.entries.items()
                    if entry.imports}

        recent = sorted(
            [(p, e.mtime) for p, e in self.entries.items()
             if self.workspace / p in [f for f in self._scannable_files()]],
            key=lambda x: x[1], reverse=True
        )[:5]

        return {
            "entry_points": sorted(entry_points),
            "source_count": len(sources),
            "test_count": len(tests),
            "config_count": len(configs),
            "total_files": len(self.entries),
            "total_loc": total_loc,
            "modules": modules,
            "dependency_graph": dep_graph,
            "recently_modified": recent,
        }

    def route_summary(self) -> list[dict]:
        """Extract all routes from indexed entries. Returns [{method, path, handler, file, lineno}]."""
        all_routes = []
        for rel, entry in self.entries.items():
            for r in entry.routes:
                all_routes.append({
                    "method": r.get("method", "?"),
                    "path": r.get("path", "/"),
                    "handler": r.get("handler", "?"),
                    "file": rel,
                    "lineno": r.get("lineno", 0),
                })
        return sorted(all_routes, key=lambda r: (r["path"], r["method"]))

    def callers(self, function_name: str) -> list[str]:
        """Find which functions call the given function."""
        callers_list = []
        for rel, entry in self.entries.items():
            for func, callees in entry.call_graph.items():
                if function_name in callees:
                    callers_list.append(f"{rel}:{func}")
        return callers_list

    def summary_text(self) -> str:
        """返回项目摘要的 Markdown 文本"""
        s = self.summary()
        lines = [
            f"项目: {self.workspace.name}",
            f"入口: {', '.join(s['entry_points']) if s['entry_points'] else 'unknown'}",
            f"文件: {s['total_files']} 个 ({s['source_count']} source, {s['test_count']} test, {s['config_count']} config)",
            f"代码行数: {s['total_loc']:,} 行",
        ]

        if s["recently_modified"]:
            lines.append(f"最近修改: {', '.join(p for p, _ in s['recently_modified'][:3])}")

        # Add route summary if available
        routes = self.route_summary()
        if routes:
            lines.append(f"\n【API 路由】({len(routes)} 个端点)")
            for r in routes[:20]:
                lines.append(f"  {r['method']:6s} {r['path']:30s} -> {r['handler']}  ({r['file']}:{r['lineno']})")
            if len(routes) > 20:
                lines.append(f"  ... 及其他 {len(routes) - 20} 个路由")

        return "\n".join(lines)

    # ── Internal ────────────────────────────────────────────────────

    def _scannable_files(self):
        """返回所有需要索引的文件"""
        skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules",
                     ".memory", ".tasks", ".team", ".snapshots",
                     ".transcripts", ".task_outputs", ".runtime-tasks",
                     ".nanocursor", "workspace"}
        skip_exts = {".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".zip",
                     ".tar", ".gz", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                     ".ico", ".woff", ".woff2"}

        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for f in files:
                fp = Path(root) / f
                if fp.suffix.lower() in skip_exts:
                    continue
                yield fp

    def _index_file(self, filepath: Path) -> Optional[FileEntry]:
        """索引单个文件"""
        try:
            stat = filepath.stat()
        except OSError:
            return None

        suffix = filepath.suffix.lower()
        role = _classify_file(filepath)

        # Language detection
        routes = []
        call_graph = {}
        if suffix in PY_EXTS:
            language = "python"
            symbols, imports, routes, call_graph = _parse_python_file(filepath)
        elif suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
            language = "javascript" if suffix.startswith(".js") else "typescript"
            symbols, imports, routes, call_graph = _parse_js_like_file(filepath)
        elif suffix in (".json", ".yaml", ".yml", ".toml"):
            language = suffix.lstrip(".")
            symbols, imports = [], []
        else:
            language = "text"
            symbols, imports = [], []

        # Count actual lines of code (exclude blank lines and comment-only lines)
        loc = 0
        if language in ("python", "javascript", "typescript", "text"):
            try:
                for line in filepath.read_text(encoding="utf-8", errors="replace").splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and not stripped.startswith("//"):
                        loc += 1
            except Exception:
                pass

        return FileEntry(
            path=str(filepath.relative_to(self.workspace)),
            role=role,
            language=language,
            symbols=symbols,
            imports=imports,
            mtime=stat.st_mtime,
            size=stat.st_size,
            loc=loc,
            routes=routes,
            call_graph=call_graph,
        )

    def _save(self):
        """保存索引到 JSON 文件"""
        data = {}
        for rel, entry in self.entries.items():
            data[rel] = {
                "path": entry.path,
                "role": entry.role,
                "language": entry.language,
                "symbols": entry.symbols,
                "imports": entry.imports,
                "mtime": entry.mtime,
                "size": entry.size,
                "loc": entry.loc,
                "routes": entry.routes,
                "call_graph": entry.call_graph,
            }
        self._ensure_dir()
        self.index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _load(self):
        """从 JSON 文件加载索引"""
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            self.entries = {}
            for rel, d in raw.items():
                self.entries[rel] = FileEntry(
                    path=d["path"],
                    role=d["role"],
                    language=d["language"],
                    symbols=d.get("symbols", []),
                    imports=d.get("imports", []),
                    mtime=d.get("mtime", 0),
                    size=d.get("size", 0),
                    loc=d.get("loc", 0),
                    routes=d.get("routes", []),
                    call_graph=d.get("call_graph", {}),
                )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[Indexer] Failed to load index: {e}")
            self.entries = {}


# ── Optional Go gRPC facade ────────────────────────────────────────


class HybridProjectIndex:
    """ProjectIndex-compatible facade that prefers Go gRPC and falls back to Python.

    The facade keeps the public ``ProjectIndex`` query surface stable for the
    rest of the Python backend. Go indexer is enabled by default but remains
    non-critical because every operation can fall back to the Python indexer.
    """

    def __init__(self, workspace: Path, server_addr: str | None = None, fallback_enabled: bool = True):
        self.workspace = Path(workspace).resolve()
        self.entries: dict[str, FileEntry] = {}
        self._fallback_enabled = fallback_enabled
        self._python = ProjectIndex(self.workspace)
        self._go = None
        try:
            from src.indexer.indexer_grpc import ProjectIndexClient

            self._go = ProjectIndexClient(self.workspace, server_addr=server_addr)
        except Exception as exc:
            if not self._fallback_enabled:
                raise
            logger.warning(f"[Indexer] Go gRPC client unavailable, using Python fallback: {exc}")

    def _fallback(self, method: str, *args, **kwargs):
        if not self._fallback_enabled:
            raise RuntimeError(f"Go indexer {method} failed and Python fallback is disabled")
        func = getattr(self._python, method)
        result = func(*args, **kwargs)
        self.entries = self._python.entries
        return result

    def _call(self, method: str, fallback: Callable, *args, **kwargs):
        if self._go is None:
            return fallback(*args, **kwargs)
        if _go_indexer_on_cooldown(self._go._addr):
            return fallback(*args, **kwargs)
        try:
            result = getattr(self._go, method)(*args, **kwargs)
            _clear_go_indexer_failure(self._go._addr)
            return result
        except Exception as exc:
            if not self._fallback_enabled:
                raise
            logger.warning(f"[Indexer] Go gRPC {method} failed, using Python fallback: {exc}")
            _mark_go_indexer_failed(self._go._addr)
            return fallback(*args, **kwargs)

    def build(self, force: bool = False) -> bool:
        return bool(self._call("build", lambda force=False: self._fallback("build", force), force))

    def update(self) -> int:
        return int(self._call("update", lambda: self._fallback("update")))

    def search_symbol(self, query: str) -> list[dict]:
        return list(self._call("search_symbol", lambda query: self._fallback("search_symbol", query), query))

    def search_dependents(self, module: str) -> list[str]:
        return list(self._call("search_dependents", lambda module: self._fallback("search_dependents", module), module))

    def summary(self) -> dict:
        return dict(self._call("summary", lambda: self._fallback("summary")))

    def route_summary(self) -> list[dict]:
        return list(self._call("route_summary", lambda: self._fallback("route_summary")))

    def callers(self, function_name: str) -> list[str]:
        return list(self._call("callers", lambda function_name: self._fallback("callers", function_name), function_name))

    def summary_text(self) -> str:
        return str(self._call("summary_text", lambda: self._fallback("summary_text")))

    def close(self) -> None:
        if self._go is not None:
            try:
                self._go.close()
            except Exception:
                pass


# 全局单例
_index: Optional[ProjectIndex | HybridProjectIndex] = None
_GO_INDEXER_DISABLED_UNTIL_BY_ADDR: dict[str, float] = {}


def _go_indexer_failure_cooldown_seconds() -> float:
    from src.runtime.runtime_feature_flags import go_indexer_failure_cooldown_seconds

    return go_indexer_failure_cooldown_seconds()


def _go_indexer_on_cooldown(address: str) -> bool:
    until = _GO_INDEXER_DISABLED_UNTIL_BY_ADDR.get(address, 0.0)
    if until <= time.monotonic():
        _GO_INDEXER_DISABLED_UNTIL_BY_ADDR.pop(address, None)
        return False
    return True


def _mark_go_indexer_failed(address: str) -> None:
    cooldown = _go_indexer_failure_cooldown_seconds()
    if cooldown > 0:
        _GO_INDEXER_DISABLED_UNTIL_BY_ADDR[address] = time.monotonic() + cooldown


def _clear_go_indexer_failure(address: str) -> None:
    _GO_INDEXER_DISABLED_UNTIL_BY_ADDR.pop(address, None)


def get_project_index(workspace: Path = None) -> ProjectIndex | HybridProjectIndex:
    global _index
    if _index is None:
        if workspace is None:
            from src.infra.config import WORKSPACE_DIR
            workspace = Path(WORKSPACE_DIR)
        from src.runtime.runtime_feature_flags import (
            go_indexer_addr,
            go_indexer_enabled,
            go_indexer_fallback_enabled,
        )

        address = go_indexer_addr()
        if go_indexer_enabled() and not _go_indexer_on_cooldown(address):
            _index = HybridProjectIndex(
                workspace,
                server_addr=address,
                fallback_enabled=go_indexer_fallback_enabled(),
            )
        else:
            _index = ProjectIndex(workspace)
    return _index


def reset_index():
    """重置全局索引（工作区切换时调用）"""
    global _index
    if hasattr(_index, "close"):
        try:
            _index.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    _index = None
    _GO_INDEXER_DISABLED_UNTIL_BY_ADDR.clear()


__all__ = ["ProjectIndex", "HybridProjectIndex", "get_project_index", "reset_index", "FileEntry"]
