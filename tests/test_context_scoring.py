"""Tests for multi-dimensional file relevance scoring in context_service."""

import math

from src.api.services.context_service import (
    _build_haystack,
    _extract_conversation_symbols,
    _score_file,
    _select_relevant_files,
    _strategy_token_budget,
)


def _make_entry(path="src/api.py", role="source", language="python",
                symbols=None, imports=None, routes=None, call_graph=None,
                mtime=0.0, size=100, loc=50):
    return {
        "path": path,
        "role": role,
        "language": language,
        "symbols": symbols or [],
        "imports": imports or [],
        "routes": routes or [],
        "call_graph": call_graph or {},
        "mtime": mtime,
        "size": size,
        "loc": loc,
    }


class TestBuildHaystack:
    def test_includes_path_role_language(self):
        entry = _make_entry(path="src/auth.py", role="source", language="python")
        haystack = _build_haystack(entry)
        assert "src/auth.py" in haystack
        assert "source" in haystack
        assert "python" in haystack

    def test_includes_symbols(self):
        entry = _make_entry(symbols=[{"name": "login"}, {"name": "logout"}])
        haystack = _build_haystack(entry)
        assert "login" in haystack
        assert "logout" in haystack

    def test_includes_imports(self):
        entry = _make_entry(imports=["flask", "requests"])
        haystack = _build_haystack(entry)
        assert "flask" in haystack
        assert "requests" in haystack


class TestScoreFile:
    def test_semantic_match_scores(self):
        entry = _make_entry(symbols=[{"name": "login_user"}])
        all_entries = {"src/auth.py": entry}
        score = _score_file(entry, {"login"}, set(), set(), all_entries)
        assert score > 0

    def test_no_match_scores_zero(self):
        entry = _make_entry(symbols=[{"name": "unrelated"}])
        all_entries = {"src/auth.py": entry}
        score = _score_file(entry, {"login"}, set(), set(), all_entries)
        assert score == 0.0

    def test_route_match_high_weight(self):
        entry = _make_entry(
            routes=[{"method": "POST", "path": "/api/users", "handler": "create_user"}]
        )
        all_entries = {"src/routes.py": entry}
        score = _score_file(entry, {"users"}, set(), set(), all_entries)
        assert score > 0

    def test_import_match(self):
        entry = _make_entry(imports=["from src.auth import login"])
        all_entries = {"src/api.py": entry}
        score = _score_file(entry, {"login"}, set(), set(), all_entries)
        assert score > 0

    def test_recent_changes_bonus(self):
        entry = _make_entry(path="src/auth.py")
        all_entries = {"src/auth.py": entry}
        score_with = _score_file(entry, {"auth"}, {"src/auth.py"}, set(), all_entries)
        score_without = _score_file(entry, {"auth"}, set(), set(), all_entries)
        assert score_with > score_without

    def test_conversation_symbols_bonus(self):
        entry = _make_entry(symbols=[{"name": "login"}])
        all_entries = {"src/auth.py": entry}
        score_with = _score_file(entry, set(), set(), {"login"}, all_entries)
        score_without = _score_file(entry, set(), set(), set(), all_entries)
        assert score_with > score_without

    def test_entry_point_role_bonus(self):
        entry = _make_entry(role="entry_point")
        all_entries = {"main.py": entry}
        score = _score_file(entry, {"main"}, set(), set(), all_entries)
        assert score > 0

    def test_length_normalization(self):
        # Short file should score higher than long file with same match
        short = _make_entry(loc=10, symbols=[{"name": "login"}])
        long = _make_entry(loc=1000, symbols=[{"name": "login"}])
        entries = {"short.py": short, "long.py": long}
        score_short = _score_file(short, {"login"}, set(), set(), entries)
        score_long = _score_file(long, {"login"}, set(), set(), entries)
        assert score_short > score_long

    def test_call_graph_callee_match(self):
        entry = _make_entry(
            call_graph={"main": ["process_login", "validate"]}
        )
        all_entries = {"src/app.py": entry}
        score = _score_file(entry, {"process_login"}, set(), set(), all_entries)
        assert score > 0

    def test_tfidf_rare_term_higher_weight(self):
        # A term that appears in fewer files should get higher IDF
        common = _make_entry(symbols=[{"name": "api"}])
        rare = _make_entry(symbols=[{"name": "zorblix"}])
        entries = {"a.py": common, "b.py": common, "c.py": rare}
        # "api" appears in 2 files, "zorblix" in 1
        score_common = _score_file(common, {"api"}, set(), set(), entries)
        score_rare = _score_file(rare, {"zorblix"}, set(), set(), entries)
        # Both match their own term, but rare should have higher IDF
        assert score_rare > score_common


class TestExtractConversationSymbols:
    def test_extracts_identifiers(self):
        result = _extract_conversation_symbols("engine.py has a bug in build_context", "")
        assert "engine" in result
        assert "build_context" in result

    def test_extracts_from_both_summaries(self):
        result = _extract_conversation_symbols("login function", "validate_user function")
        assert "login" in result
        assert "validate_user" in result

    def test_empty_input(self):
        result = _extract_conversation_symbols("", "")
        assert result == set()

    def test_skips_short_tokens(self):
        result = _extract_conversation_symbols("if x do y", "")
        # "x", "do", "y" are too short
        assert not any(len(s) < 3 for s in result)


class TestSelectRelevantFiles:
    def test_returns_matches(self):
        entries = {
            "src/auth.py": _make_entry(symbols=[{"name": "login"}]),
            "src/unrelated.py": _make_entry(symbols=[{"name": "unrelated"}]),
        }
        index_data = {"entries": entries, "entry_points": [], "recently_modified": []}
        result = _select_relevant_files("fix the login bug", index_data, None)
        assert "src/auth.py" in result

    def test_fallback_when_no_match(self):
        index_data = {
            "entries": {"src/x.py": _make_entry(symbols=[{"name": "zzz"}])},
            "entry_points": ["main.py"],
            "recently_modified": [("recent.py", 0)],
        }
        result = _select_relevant_files("unmatchable query", index_data, None)
        assert "main.py" in result

    def test_recent_changes_boost(self):
        entries = {
            "src/a.py": _make_entry(symbols=[{"name": "login"}]),
            "src/b.py": _make_entry(symbols=[{"name": "login"}]),
        }
        index_data = {"entries": entries, "entry_points": [], "recently_modified": []}
        result = _select_relevant_files(
            "login", index_data, None,
            recent_changes={"src/a.py"},
        )
        # a.py should rank higher due to recent_changes bonus
        assert result[0] == "src/a.py"


class TestStrategyTokenBudget:
    def test_analysis_only(self):
        assert _strategy_token_budget("analysis_only") == 15000

    def test_docs_only(self):
        assert _strategy_token_budget("docs_only") == 8000

    def test_small_patch(self):
        assert _strategy_token_budget("small_patch") == 10000

    def test_feature_delivery_default(self):
        assert _strategy_token_budget("feature_delivery") == 12000

    def test_unknown_strategy_defaults(self):
        assert _strategy_token_budget("unknown_strategy") == 12000
