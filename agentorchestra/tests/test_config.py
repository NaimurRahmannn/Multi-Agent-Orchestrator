
import pytest
from pydantic import SecretStr

from agentorchestra.config import (
    ConfigurationError,
    Settings,
    _ensure_inside_root,
    ensure_runtime_directories,
    get_settings,
)


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

    with pytest.raises(ConfigurationError) as error:
        settings.require_groq_configuration()

    assert "GROQ_API_KEY" in str(error.value)
    assert "GROQ_MODEL" not in str(error.value)


def test_groq_validation_fails_clearly_without_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    settings = Settings()

    with pytest.raises(ConfigurationError) as error:
        settings.require_groq_configuration()

    assert "GROQ_MODEL" in str(error.value)
    assert "test-secret" not in str(error.value)


def test_environment_overrides_work(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("UNKNOWN_SETTING", "ignored")

    settings = Settings()
    groq = settings.require_groq_configuration()

    assert settings.groq_api_key == SecretStr("test-secret")
    assert groq.api_key == "test-secret"
    assert groq.model == "test-model"
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"


def test_cached_settings_can_be_cleared(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "first")
    get_settings.cache_clear()
    assert get_settings().app_env == "first"

    monkeypatch.setenv("APP_ENV", "second")
    assert get_settings().app_env == "first"

    get_settings.cache_clear()
    assert get_settings().app_env == "second"


def test_project_root_override_and_derived_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTORCHESTRA_ROOT", str(tmp_path))

    settings = Settings()

    assert settings.project_root == tmp_path.resolve()
    assert settings.source_dir == tmp_path / "src"
    assert settings.fixture_site_dir == tmp_path / "sites" / "fixture"
    assert settings.working_site_dir == tmp_path / "sites" / "working"
    assert settings.staging_root_dir == tmp_path / "sites" / "staging"
    assert settings.reports_root_dir == tmp_path / "reports"
    assert settings.lighthouse_report_dir == tmp_path / "reports" / "lighthouse"
    assert settings.screenshot_report_dir == tmp_path / "reports" / "screenshots"
    assert settings.routing_report_dir == tmp_path / "reports" / "routing"


def test_paths_cannot_escape_project_root(tmp_path):
    with pytest.raises(ConfigurationError):
        _ensure_inside_root(tmp_path, tmp_path.parent)


def test_runtime_directory_helper_creates_only_allowed_directories(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTORCHESTRA_ROOT", str(tmp_path))
    settings = Settings()

    ensure_runtime_directories(settings)

    assert settings.staging_root_dir.is_dir()
    assert settings.lighthouse_report_dir.is_dir()
    assert settings.screenshot_report_dir.is_dir()
    assert settings.routing_report_dir.is_dir()
    assert not settings.fixture_site_dir.exists()
    assert not settings.working_site_dir.exists()


def test_runtime_directory_helper_does_not_modify_fixture_or_working(monkeypatch, tmp_path):
    fixture = tmp_path / "sites" / "fixture"
    working = tmp_path / "sites" / "working"
    fixture.mkdir(parents=True)
    working.mkdir(parents=True)
    (fixture / "index.html").write_text("fixture", encoding="utf-8")
    (working / "index.html").write_text("working", encoding="utf-8")
    monkeypatch.setenv("AGENTORCHESTRA_ROOT", str(tmp_path))

    ensure_runtime_directories(Settings())

    assert (fixture / "index.html").read_text(encoding="utf-8") == "fixture"
    assert (working / "index.html").read_text(encoding="utf-8") == "working"


def test_secret_values_are_not_leaked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    settings = Settings()

    assert "super-secret-key" not in repr(settings)
    assert "super-secret-key" not in str(settings.model_dump(mode="json"))
    with pytest.raises(ConfigurationError) as error:
        settings.require_groq_configuration()
    assert "super-secret-key" not in str(error.value)
