from pydantic import SecretStr

from agentorchestra.config import ConfigurationError, Settings, get_settings


def test_settings_load_without_groq_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.groq_api_key is None
    assert settings.app_env == "development"


def test_groq_validation_fails_clearly_without_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_MODEL", "llama-test")

    settings = Settings()

    try:
        settings.require_groq()
    except ConfigurationError as exc:
        message = str(exc)
    else:
        raise AssertionError("require_groq should fail without GROQ_API_KEY")

    assert "GROQ_API_KEY" in message
    assert "GROQ_MODEL" not in message


def test_environment_overrides_work(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("UNKNOWN_SETTING", "ignored")

    settings = Settings()

    assert settings.groq_api_key == SecretStr("test-secret")
    assert settings.groq_model == "test-model"
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"


def test_secret_values_are_not_leaked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    settings = Settings()

    assert "super-secret-key" not in repr(settings)
    try:
        settings.require_groq()
    except ConfigurationError as exc:
        assert "super-secret-key" not in str(exc)
    else:
        raise AssertionError("require_groq should fail without GROQ_MODEL")
