from pathlib import Path

import pytest
import yaml

from nanocursor.config import _from_raw, _merge_raw, load_config
from nanocursor.validator import ConfigError

PROFILE = {'name': 'test', 'protocol': 'openai-compat', 'base_url': 'https://example.invalid', 'model': 'fake'}


def test_optional_features_default_off_and_explicit_false_survives_merge():
    config = _from_raw({'providers': [PROFILE], 'enable_fork': True, 'enable_coordinator_mode': True})
    assert not config.enable_teams and not config.memory_consolidation_enabled
    merged = _merge_raw({'providers': [PROFILE], 'enable_teams': True,
                         'memory': {'consolidation': {'enabled': True}}},
                        {'enable_teams': False, 'memory': {'consolidation': {'enabled': False}}})
    effective = _from_raw(merged)
    assert not effective.enable_teams and not effective.memory_consolidation_enabled


@pytest.mark.parametrize('field', [{'enable_teams': 'false'}, {'memory': {'consolidation': {'enabled': 'false'}}},
                                 {'memory': {'consolidation': {'unknown': True}}}, {'memory': False}])
def test_experimental_config_rejects_ambiguous_values(field):
    with pytest.raises(ConfigError):
        _from_raw({'providers': [PROFILE], **field})


def test_project_cannot_enable_background_memory_without_user_setting(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    project = tmp_path / 'project'
    (project / '.nanocursor').mkdir(parents=True)
    monkeypatch.setenv('NANOCURSOR_HOME', str(home))
    (home / 'config.yaml').write_text(yaml.safe_dump({'providers': [PROFILE]}))
    target = project / '.nanocursor/config.yaml'
    target.write_text('memory:\n  consolidation:\n    enabled: true\n')
    with pytest.raises(ConfigError, match='user configuration'):
        load_config(work_dir=project)
    (home / 'config.yaml').write_text(yaml.safe_dump({'providers': [PROFILE], 'memory': {'consolidation': {'enabled': True}}}))
    assert load_config(work_dir=project).memory_consolidation_enabled
    target.write_text('memory:\n  consolidation:\n    enabled: false\n')
    assert not load_config(work_dir=project).memory_consolidation_enabled
