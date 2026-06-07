from src.infra.config import AppConfig, load_app_config


def test_app_config_uses_model_defaults_without_environment():
    loaded = load_app_config({})

    assert loaded == AppConfig()


def test_app_config_parses_valid_environment_values():
    loaded = load_app_config(
        {
            "MAX_CONCURRENT_RUNS": " 9 ",
            "LLM_TEMPERATURE": "0.7",
            "SUPERVISOR_MODEL": " qwen-plus ",
        }
    )

    assert loaded.MAX_CONCURRENT_RUNS == 9
    assert loaded.LLM_TEMPERATURE == 0.7
    assert loaded.SUPERVISOR_MODEL == "qwen-plus"


def test_app_config_falls_back_only_for_invalid_field():
    loaded = load_app_config(
        {
            "MAX_CONCURRENT_RUNS": "999",
            "LLM_MAX_TOKENS": "8192",
        }
    )

    assert loaded.MAX_CONCURRENT_RUNS == AppConfig().MAX_CONCURRENT_RUNS
    assert loaded.LLM_MAX_TOKENS == 8192
