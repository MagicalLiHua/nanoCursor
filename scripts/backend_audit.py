#!/usr/bin/env python3
"""Backend API audit script — generates docs/backend-audit-report.md.

Traverses all registered routes, checks for duplicates, identifies routes that
are still defined in legacy runtime modules, and flags routes using bare dict
parameters.
"""

from __future__ import annotations

import inspect
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.api.server import app
from src.agent.engine import ALL_TOOLS
from src.api.legacy_contracts import (
    ALLOWED_LEGACY_WORKFLOW_ADAPTER_CONSUMERS,
    RETAINED_COMPATIBILITY_ROUTES,
    RETIRED_API_ROUTES,
    RETIRED_IMPLEMENTATION_PATHS,
    RETIRED_MODEL_TOOLS,
    RETIRED_PRODUCT_IMPORTS,
)


def collect_routes():
    """Collect all routes from the FastAPI app."""
    routes_info: list[dict] = []
    seen = set()

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = tuple(sorted(getattr(route, "methods", []) or []))
        endpoint = getattr(route, "endpoint", None)
        name = getattr(route, "name", getattr(endpoint, "__name__", str(route)))

        # Skip duplicates (mounted sub-apps may register twice)
        key = (path, methods, name)
        if key in seen:
            continue
        seen.add(key)

        module = ""
        file_path = ""
        if endpoint:
            try:
                module = inspect.getmodule(endpoint).__name__ if inspect.getmodule(endpoint) else ""
                file_path = inspect.getfile(endpoint)
            except (TypeError, OSError):
                pass

        routes_info.append({
            "path": path,
            "methods": list(methods),
            "name": name,
            "module": module,
            "file": file_path,
            "in_legacy_runtime": file_path.endswith("legacy_runtime.py"),
        })

    return routes_info


def find_duplicates(routes: list[dict]):
    """Find duplicate (path, methods) registrations."""
    groups = defaultdict(list)
    for r in routes:
        key = (r["path"], tuple(sorted(r["methods"])))
        groups[key].append(r)

    return {k: v for k, v in groups.items() if len(v) > 1}


def find_bare_dict_routes(routes: list[dict]):
    """Find routes whose handler accepts a bare dict parameter."""
    bare = []
    for r in routes:
        endpoint = None
        for route in app.routes:
            if getattr(route, "name", None) == r["name"]:
                endpoint = getattr(route, "endpoint", None)
                break
        if not endpoint:
            continue
        try:
            sig = inspect.signature(endpoint)
        except (ValueError, TypeError):
            continue
        for param_name, param in sig.parameters.items():
            if param_name in ("request", "req"):
                continue
            ann = param.annotation
            if ann is inspect.Parameter.empty:
                continue
            if ann is dict:
                bare.append({**r, "param": param_name})
                break
    return bare


def find_registered_route_contracts(routes: list[dict], contracts: frozenset[tuple[str, str]]) -> list[dict]:
    """Return registered routes matching an explicit route contract set."""
    matches = []
    for route in routes:
        for method in route["methods"]:
            if (method, route["path"]) in contracts:
                matches.append(route)
                break
    return matches


def find_retired_model_tools() -> list[str]:
    """Return retired tools that are still exposed to the Lead model."""
    exposed = {
        str(tool.get("name"))
        for tool in ALL_TOOLS
        if isinstance(tool, dict) and tool.get("name")
    }
    return sorted(exposed & RETIRED_MODEL_TOOLS)


def find_retired_product_imports() -> list[tuple[str, str]]:
    """Return product runtime modules that import a retired implementation."""
    offenders: list[tuple[str, str]] = []
    for package in ("src/api", "src/runtime", "src/agent"):
        for path in sorted((Path(ROOT) / package).rglob("*.py")):
            if path.name == "legacy_contracts.py":
                continue
            text = path.read_text(encoding="utf-8")
            for module in RETIRED_PRODUCT_IMPORTS:
                if f"from {module} import" in text or f"import {module}" in text:
                    offenders.append((str(path.relative_to(ROOT)), module))
    return offenders


def find_retired_implementation_paths() -> list[str]:
    """Return removed implementation paths that have reappeared."""
    return sorted(path for path in RETIRED_IMPLEMENTATION_PATHS if (Path(ROOT) / path).exists())


def find_secondary_run_manager_owners() -> list[str]:
    """Return API modules that instantiate a second process-wide RunManager."""
    offenders = []
    for path in sorted((Path(ROOT) / "src" / "api").rglob("*.py")):
        if path.name == "runtime_registry_service.py":
            continue
        if "RunManager()" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))
    return offenders


def find_legacy_workflow_adapter_consumers() -> tuple[list[str], list[str]]:
    """Return all and unexpected imports of the shrinking workflow adapter."""
    consumers = []
    for path in sorted((Path(ROOT) / "src" / "api").rglob("*.py")):
        if path.name == "runtime_facade.py":
            continue
        relative = str(path.relative_to(ROOT))
        if "from src.api.runtime_facade import" in path.read_text(encoding="utf-8"):
            consumers.append(relative)
    unexpected = sorted(set(consumers) - ALLOWED_LEGACY_WORKFLOW_ADAPTER_CONSUMERS)
    return consumers, unexpected


def generate_report(
    routes: list[dict],
    duplicates: dict,
    bare_dict: list[dict],
    retired_routes: list[dict],
    compatibility_routes: list[dict],
    retired_tools: list[str],
    retired_imports: list[tuple[str, str]],
    retired_implementation_paths: list[str],
    secondary_run_managers: list[str],
    workflow_adapter_consumers: list[str],
    unexpected_workflow_adapter_consumers: list[str],
):
    """Generate Markdown report."""
    api_routes = [r for r in routes if r["path"].startswith("/api/")]
    non_api_routes = [r for r in routes if not r["path"].startswith("/api/")]

    in_legacy = [r for r in api_routes if r["in_legacy_runtime"]]
    modular = [r for r in api_routes if not r["in_legacy_runtime"]]

    lines = [
        "# nanoCursor Backend API Audit Report",
        "",
        f"> Generated by `scripts/backend_audit.py`",
        f"> Total routes: {len(routes)}  |  API routes: {len(api_routes)}  |  In legacy runtime: {len(in_legacy)}",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|---|---|",
        f"| Total registered routes | {len(routes)} |",
        f"| API routes (`/api/*`) | {len(api_routes)} |",
        f"| Non-API routes (health, mounts) | {len(non_api_routes)} |",
        f"| Routes still in legacy runtime | {len(in_legacy)} |",
        f"| Routes in modular `src/api/routes/*` | {len(modular)} |",
        f"| Duplicate route groups | {len(duplicates)} |",
        f"| Routes with bare `dict` parameter | {len(bare_dict)} |",
        f"| Retired routes still registered | {len(retired_routes)} |",
        f"| Retired model tools still exposed | {len(retired_tools)} |",
        f"| Product imports of retired modules | {len(retired_imports)} |",
        f"| Retired implementation paths present | {len(retired_implementation_paths)} |",
        f"| Secondary API RunManager owners | {len(secondary_run_managers)} |",
        f"| Legacy workflow adapter consumers | {len(workflow_adapter_consumers)} |",
        f"| Unexpected workflow adapter consumers | {len(unexpected_workflow_adapter_consumers)} |",
        f"| Retained compatibility routes | {len(compatibility_routes)} |",
        "",
    ]

    if duplicates:
        lines.append("## Duplicate Routes (ERROR)")
        lines.append("")
        for (path, methods), entries in duplicates.items():
            methods_str = ", ".join(methods) if methods else "ANY"
            lines.append(f"### `{methods_str} {path}`")
            for e in entries:
                lines.append(f"- `{e['name']}` in `{e['module'] or 'unknown'}`")
            lines.append("")
    else:
        lines.append("## Duplicate Routes")
        lines.append("")
        lines.append("No duplicate routes found.")
        lines.append("")

    if bare_dict:
        lines.append("## Routes with Bare `dict` Parameters (WARNING)")
        lines.append("")
        lines.append("These routes should be migrated to Pydantic request models:")
        lines.append("")
        lines.append("| Method | Path | Handler | Param | File |")
        lines.append("|---|---|---|---|---|")
        for r in bare_dict:
            methods = ", ".join(r["methods"])
            lines.append(f"| {methods} | `{r['path']}` | `{r['name']}` | `{r.get('param', '?')}` | `{r['module']}` |")
        lines.append("")
    else:
        lines.append("## Routes with Bare `dict` Parameters")
        lines.append("")
        lines.append("No bare dict parameters found in API routes.")
        lines.append("")

    lines.append("## Retirement Boundary")
    lines.append("")
    if retired_routes:
        lines.append("Retired routes are registered again (ERROR):")
        lines.append("")
        for route in retired_routes:
            lines.append(f"- `{', '.join(route['methods'])} {route['path']}` in `{route['module']}`")
    else:
        lines.append("No retired API routes are registered.")
    lines.append("")
    if retired_tools:
        lines.append("Retired model tools are exposed again (ERROR):")
        lines.append("")
        for tool in retired_tools:
            lines.append(f"- `{tool}`")
    else:
        lines.append("No retired model tools are exposed.")
    lines.append("")
    if retired_imports:
        lines.append("Product runtime modules import retired implementations (ERROR):")
        lines.append("")

    if retired_implementation_paths:
        lines.append("## Retired Implementation Paths Present (ERROR)")
        lines.append("")
        lines.extend(f"- `{path}`" for path in retired_implementation_paths)
        lines.append("")
        for path, module in retired_imports:
            lines.append(f"- `{path}` imports `{module}`")
    else:
        lines.append("No product runtime modules import retired implementations.")
    lines.append("")
    if secondary_run_managers:
        lines.append("API modules instantiate secondary RunManager registries (ERROR):")
        lines.append("")
        for path in secondary_run_managers:
            lines.append(f"- `{path}`")
    else:
        lines.append("The runtime registry is the only API RunManager owner.")
    lines.append("")
    if unexpected_workflow_adapter_consumers:
        lines.append("Unexpected modules import the legacy workflow adapter (ERROR):")
        lines.append("")
        for path in unexpected_workflow_adapter_consumers:
            lines.append(f"- `{path}`")
    else:
        lines.append("Legacy workflow adapter consumers remain inside the explicit shrinking boundary.")
    lines.append("")

    lines.append("## Retained Compatibility Routes")
    lines.append("")
    if compatibility_routes:
        for route in compatibility_routes:
            lines.append(f"- `{', '.join(route['methods'])} {route['path']}`")
    else:
        lines.append("No compatibility aliases are currently registered.")
    lines.append("")

    lines.append("## Routes in Legacy Runtime (Not Yet Modularized)")
    lines.append("")
    if in_legacy:
        lines.append("| Method | Path | Handler |")
        lines.append("|---|---|---|")
        for r in sorted(in_legacy, key=lambda x: x["path"]):
            methods = ", ".join(r["methods"])
            lines.append(f"| {methods} | `{r['path']}` | `{r['name']}` |")
    else:
        lines.append("All routes are modularized.")
    lines.append("")

    lines.append("## Modular Routes (in `src/api/routes/*`)")
    lines.append("")
    if modular:
        by_module = defaultdict(list)
        for r in modular:
            by_module[r["module"]].append(r)
        for mod, mod_routes in sorted(by_module.items()):
            lines.append(f"### `{mod}`")
            lines.append("")
            lines.append("| Method | Path | Handler |")
            lines.append("|---|---|---|")
            for r in sorted(mod_routes, key=lambda x: x["path"]):
                methods = ", ".join(r["methods"])
                lines.append(f"| {methods} | `{r['path']}` | `{r['name']}` |")
            lines.append("")
    else:
        lines.append("No modular routes yet.")
    lines.append("")

    lines.append("## Non-API Routes")
    lines.append("")
    lines.append("| Method | Path | Name |")
    lines.append("|---|---|---|")
    for r in non_api_routes:
        methods = ", ".join(r["methods"])
        lines.append(f"| {methods} | `{r['path']}` | `{r['name']}` |")
    lines.append("")

    lines.append("## Full API Route Inventory")
    lines.append("")
    lines.append("| Method | Path | Handler | Module | In legacy runtime? |")
    lines.append("|---|---|---|---|---|")
    for r in sorted(api_routes, key=lambda x: (x["path"], ", ".join(x["methods"]))):
        methods = ", ".join(r["methods"]) if r["methods"] else "ANY"
        lines.append(f"| {methods} | `{r['path']}` | `{r['name']}` | `{r['module'] or 'unknown'}` | {'YES' if r['in_legacy_runtime'] else 'no'} |")
    lines.append("")

    return "\n".join(lines)


def main():
    routes = collect_routes()
    duplicates = find_duplicates(routes)
    bare_dict = find_bare_dict_routes(routes)
    retired_routes = find_registered_route_contracts(routes, RETIRED_API_ROUTES)
    compatibility_routes = find_registered_route_contracts(routes, RETAINED_COMPATIBILITY_ROUTES)
    retired_tools = find_retired_model_tools()
    retired_imports = find_retired_product_imports()
    retired_implementation_paths = find_retired_implementation_paths()
    secondary_run_managers = find_secondary_run_manager_owners()
    workflow_adapter_consumers, unexpected_workflow_adapter_consumers = find_legacy_workflow_adapter_consumers()

    report = generate_report(
        routes,
        duplicates,
        bare_dict,
        retired_routes,
        compatibility_routes,
        retired_tools,
        retired_imports,
        retired_implementation_paths,
        secondary_run_managers,
        workflow_adapter_consumers,
        unexpected_workflow_adapter_consumers,
    )

    docs_dir = Path(ROOT) / "docs"
    docs_dir.mkdir(exist_ok=True)
    report_path = docs_dir / "backend-audit-report.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"Audit complete. Report written to {report_path}")
    print(f"  Total routes: {len(routes)}")
    print(f"  API routes: {len([r for r in routes if r['path'].startswith('/api/')])}")
    print(f"  In legacy runtime: {len([r for r in routes if r['in_legacy_runtime']])}")
    print(f"  Duplicates: {len(duplicates)}")
    print(f"  Bare dict params: {len(bare_dict)}")
    print(f"  Retired routes registered: {len(retired_routes)}")
    print(f"  Retired model tools exposed: {len(retired_tools)}")
    print(f"  Product imports of retired modules: {len(retired_imports)}")
    print(f"  Retired implementation paths present: {len(retired_implementation_paths)}")
    print(f"  Secondary API RunManager owners: {len(secondary_run_managers)}")
    print(f"  Legacy workflow adapter consumers: {len(workflow_adapter_consumers)}")
    print(f"  Unexpected workflow adapter consumers: {len(unexpected_workflow_adapter_consumers)}")

    if duplicates:
        print("\n  DUPLICATE ROUTES FOUND:")
        for (path, methods), entries in duplicates.items():
            print(f"    {methods} {path}")
        return 1

    if (
        retired_routes
        or retired_tools
        or retired_imports
        or retired_implementation_paths
        or secondary_run_managers
        or unexpected_workflow_adapter_consumers
    ):
        print("\n  RETIREMENT BOUNDARY VIOLATIONS:")
        for route in retired_routes:
            print(f"    route: {route['methods']} {route['path']}")
        for tool in retired_tools:
            print(f"    tool: {tool}")
        for path, module in retired_imports:
            print(f"    import: {path} -> {module}")
        for path in retired_implementation_paths:
            print(f"    retired implementation: {path}")
        for path in secondary_run_managers:
            print(f"    secondary RunManager: {path}")
        for path in unexpected_workflow_adapter_consumers:
            print(f"    unexpected workflow adapter consumer: {path}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
