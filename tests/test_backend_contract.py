"""Backend contract tests — verify no duplicate routes, error format, core routes."""

from collections import defaultdict
from pathlib import Path

from fastapi.testclient import TestClient


def test_no_duplicate_routes():
    """Ensure no duplicate (path, method) registrations exist."""
    from src.api.server import app

    routes = defaultdict(list)
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = tuple(sorted(getattr(route, "methods", []) or []))
        if path.startswith("/api/"):
            routes[(path, methods)].append(getattr(route, "name", str(route)))

    duplicates = {k: v for k, v in routes.items() if len(v) > 1}
    assert duplicates == {}, f"Duplicate routes found: {duplicates}"


def test_all_api_routes_return_json_for_errors():
    """API routes return JSON-parseable error bodies for bad requests."""
    from src.api.server import app

    client = TestClient(app, raise_server_exceptions=False)

    # Non-existent run should return JSON error
    resp = client.get("/api/runs/nonexistent_run_12345")
    assert resp.status_code in (200, 404)
    data = resp.json()
    # Either a valid response or an error with recognizable structure
    assert isinstance(data, (dict, list))


def test_core_routes_registered():
    """Core API surface is registered."""
    from src.api.server import app

    client = TestClient(app)

    # Health
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code in (200, 503)

    # Workspace
    assert client.get("/api/workspace/health").status_code == 200

    # System
    assert client.get("/api/system/version").status_code == 200
    assert client.get("/api/system/doctor").status_code == 200

    # Capabilities
    assert client.get("/api/capabilities").status_code == 200

    # Metrics
    assert client.get("/api/metrics").status_code == 200


def test_health_endpoints():
    """Health and readiness probes return expected fields."""
    from src.api.server import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_request_id_header():
    """Every response includes x-request-id header."""
    from src.api.server import app

    client = TestClient(app)
    resp = client.get("/health")
    assert "x-request-id" in resp.headers


def test_api_routes_use_runtime_facade_for_legacy_runtime_access():
    """Route modules should not import the legacy root runtime directly."""
    root = Path(__file__).resolve().parents[1]
    targets = [*sorted((root / "src" / "api" / "routes").glob("*.py")), root / "src" / "api" / "dependencies.py"]
    offenders = []
    legacy_import = "import " + "api_server"
    legacy_from_import = "from " + "api_server import"
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if legacy_import in text or legacy_from_import in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


def test_retired_api_routes_are_not_registered():
    """Deleted compatibility surfaces must not silently return to the product API."""
    from src.api.server import app
    from src.api.legacy_contracts import RETIRED_API_ROUTES

    registered = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "path")
        for method in (getattr(route, "methods", None) or [])
    }
    assert registered.isdisjoint(RETIRED_API_ROUTES)


def test_retired_model_tools_are_not_exposed():
    """The Lead model should only see the current task-board and Agent runtime tools."""
    from src.agent.engine import ALL_TOOLS
    from src.api.legacy_contracts import RETIRED_MODEL_TOOLS

    exposed = {
        str(tool.get("name"))
        for tool in ALL_TOOLS
        if isinstance(tool, dict) and tool.get("name")
    }
    assert exposed.isdisjoint(RETIRED_MODEL_TOOLS)


def test_agent_runtime_uses_canonical_file_ops_not_legacy_file_tools():
    """Model-facing Agent runtime must not route file writes through the legacy module."""
    from src.api.legacy_contracts import CANONICAL_FILE_TOOL_MODULE, LEGACY_FILE_TOOL_MODULE

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted((root / "src" / "agent").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if f"from {LEGACY_FILE_TOOL_MODULE} import" in text or f"import {LEGACY_FILE_TOOL_MODULE}" in text:
            offenders.append(str(path.relative_to(root)))

    engine_text = (root / "src" / "agent" / "engine.py").read_text(encoding="utf-8")
    assert offenders == []
    assert CANONICAL_FILE_TOOL_MODULE in engine_text


def test_product_runtime_does_not_import_retired_modules():
    """Legacy implementations may remain on disk, but current product code must not depend on them."""
    from src.api.legacy_contracts import RETIRED_PRODUCT_IMPORTS

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for package in ("src/api", "src/runtime", "src/agent"):
        for path in sorted((root / package).rglob("*.py")):
            if path.name == "legacy_contracts.py":
                continue
            text = path.read_text(encoding="utf-8")
            for module in RETIRED_PRODUCT_IMPORTS:
                if f"from {module} import" in text or f"import {module}" in text:
                    offenders.append((str(path.relative_to(root)), module))

    assert offenders == []


def test_retired_storage_implementations_stay_removed():
    """Removed SQLite and Markdown-memory owners must not quietly return."""
    from src.api.legacy_contracts import RETIRED_IMPLEMENTATION_PATHS

    root = Path(__file__).resolve().parents[1]

    assert [path for path in RETIRED_IMPLEMENTATION_PATHS if (root / path).exists()] == []


def test_official_asgi_entrypoint_does_not_import_root_compatibility_shim():
    """The supported server entrypoint must be package-owned."""
    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "api" / "server.py").read_text(encoding="utf-8")

    assert "import " + "api_server" not in text
    assert "from " + "api_server import" not in text
    assert "create_app()" in text


def test_root_api_server_shim_is_removed():
    """The repository root must not grow a compatibility api_server.py again."""
    root = Path(__file__).resolve().parents[1]

    assert not (root / "api_server.py").exists()

    offenders = []
    legacy_import = "import " + "api_server"
    legacy_from_import = "from " + "api_server import"
    for folder in ("src", "tests", "scripts"):
        for path in sorted((root / folder).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if legacy_import in text or legacy_from_import in text:
                offenders.append(str(path.relative_to(root)))

    assert offenders == []


def test_runtime_registry_is_the_only_api_run_manager_owner():
    """API modules must share one process-wide RunManager."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted((root / "src" / "api").rglob("*.py")):
        if path.name == "runtime_registry_service.py":
            continue
        if "RunManager()" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


def test_step_g_routes_do_not_use_generic_legacy_runtime_facade():
    """State-oriented and deterministic routes must stay independent of legacy runtime."""
    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "src" / "api" / "routes" / "run_entry.py",
        root / "src" / "api" / "routes" / "runs.py",
        root / "src" / "api" / "routes" / "demo_runs.py",
        root / "src" / "api" / "routes" / "benchmarks.py",
    ]
    offenders = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if "legacy_runtime" in text or "legacy_runtime as" in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


def test_runtime_facade_only_exposes_legacy_workflow_adapters():
    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "api" / "runtime_facade.py").read_text(encoding="utf-8")

    assert "def legacy_runtime" not in text
    assert "__getattr__" not in text
    assert "run_demo_workflow" not in text


def test_legacy_workflow_adapter_consumers_stay_inside_explicit_boundary():
    from src.api.legacy_contracts import ALLOWED_LEGACY_WORKFLOW_ADAPTER_CONSUMERS

    root = Path(__file__).resolve().parents[1]
    consumers = set()
    for path in sorted((root / "src" / "api").rglob("*.py")):
        if path.name == "runtime_facade.py":
            continue
        if "from src.api.runtime_facade import" in path.read_text(encoding="utf-8"):
            consumers.add(str(path.relative_to(root)))

    assert consumers == ALLOWED_LEGACY_WORKFLOW_ADAPTER_CONSUMERS


def test_step_h_legacy_runtime_delegates_core_executor():
    """The core workflow executor belongs to the service layer, not legacy runtime."""
    root = Path(__file__).resolve().parents[1]
    legacy_text = (root / "src" / "api" / "legacy_runtime.py").read_text(encoding="utf-8")
    executor_text = (root / "src" / "api" / "services" / "runtime_executor_service.py").read_text(encoding="utf-8")

    assert "_executor_run_workflow_async(" in legacy_text
    assert "_executor_run_workflow_async_from_messages(" in legacy_text
    assert "async def run_workflow_async(" in executor_text
    assert "async def run_workflow_async_from_messages(" in executor_text

    executor_only_terms = [
        "stream_model_response(",
        "RuntimeToolCallbacks(",
        "execute_lightweight_runtime_route(",
        "complete_workflow_run(",
        "fail_workflow_run(",
        "finalize_delivery_best_effort(",
    ]
    offenders = [term for term in executor_only_terms if term in legacy_text]
    assert offenders == []


def test_step_k_legacy_runtime_has_no_retired_route_aliases():
    """Legacy runtime should not keep old route function aliases around."""
    root = Path(__file__).resolve().parents[1]
    legacy_text = (root / "src" / "api" / "legacy_runtime.py").read_text(encoding="utf-8")

    retired_aliases = [
        "async def start_run(",
        "async def stream_events(",
        "async def start_agenthub_run(",
        "async def create_agenthub_conversation_run(",
    ]
    offenders = [name for name in retired_aliases if name in legacy_text]

    assert offenders == []


def test_api_app_imports_use_public_server_entrypoint_by_default():
    """Most API tests should exercise the public ASGI app instead of legacy runtime."""
    root = Path(__file__).resolve().parents[1]
    legacy_app_import = "from src.api." + "legacy_runtime import app"
    offenders = []

    for path in sorted((root / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if legacy_app_import in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []
