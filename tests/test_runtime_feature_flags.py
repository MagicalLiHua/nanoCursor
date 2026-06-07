from src.runtime.runtime_feature_flags import go_filetools_enabled, go_indexer_enabled


def test_go_filetools_enabled_by_default(monkeypatch):
    monkeypatch.delenv("NANOCURSOR_GO_FILETOOLS_ENABLED", raising=False)

    assert go_filetools_enabled() is True


def test_go_filetools_can_be_disabled(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ENABLED", "false")

    assert go_filetools_enabled() is False


def test_go_indexer_enabled_by_default(monkeypatch):
    monkeypatch.delenv("NANOCURSOR_GO_INDEXER_ENABLED", raising=False)

    assert go_indexer_enabled() is True


def test_go_indexer_can_be_disabled(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "false")

    assert go_indexer_enabled() is False
