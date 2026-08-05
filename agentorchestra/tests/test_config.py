import pytest
from pydantic import SecretStr

from agentorchestra.config import (
    ConfigurationError,
    GroqAgentName,
    Settings,
    _ensure_inside_root,
    ensure_runtime_directories,
    get_settings,
)


def test_settings_load_without_groq_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_MANAGER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_HTML_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_CSS_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_SEO_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_QA_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MANAGER_MODEL", raising=False)
    monkeypatch.delenv("GROQ_HTML_MODEL", raising=False)
    monkeypatch.delenv("GROQ_CSS_MODEL", raising=False)
    monkeypatch.delenv("GROQ_SEO_MODEL", raising=False)
    monkeypatch.delenv("GROQ_QA_MODEL", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.groq_manager_api_key is None
    assert settings.groq_html_api_key is None
    assert settings.groq_css_api_key is None
    assert settings.groq_seo_api_key is None
    assert settings.groq_qa_api_key is None
    assert settings.groq_manager_model is None
    assert settings.groq_html_model is None
    assert settings.groq_css_model is None
    assert settings.groq_seo_model is None
    assert settings.groq_qa_model is None
    assert settings.app_env == "development"


def test_groq_validation_fails_clearly_without_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_MANAGER_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_MANAGER_MODEL", "manager-model")

    settings = Settings()

    with pytest.raises(ConfigurationError) as error:
        settings.require_groq_configuration(GroqAgentName.MANAGER)

    assert "GROQ_MANAGER_API_KEY" in str(error.value)
    assert "GROQ_MANAGER_MODEL" not in str(error.value)


def test_groq_validation_fails_clearly_without_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_MANAGER_API_KEY", "manager-secret")
    monkeypatch.delenv("GROQ_MANAGER_MODEL", raising=False)

    settings = Settings()

    with pytest.raises(ConfigurationError) as error:
        settings.require_groq_configuration(GroqAgentName.MANAGER)

    assert "GROQ_MANAGER_MODEL" in str(error.value)
    assert "manager-secret" not in str(error.value)


def test_environment_overrides_work(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_MANAGER_API_KEY", "manager-secret")
    monkeypatch.setenv("GROQ_HTML_API_KEY", "html-secret")
    monkeypatch.setenv("GROQ_CSS_API_KEY", "css-secret")
    monkeypatch.setenv("GROQ_SEO_API_KEY", "seo-secret")
    monkeypatch.setenv("GROQ_QA_API_KEY", "qa-secret")
    monkeypatch.setenv("GROQ_MANAGER_MODEL", "manager-model")
    monkeypatch.setenv("GROQ_HTML_MODEL", "html-model")
    monkeypatch.setenv("GROQ_CSS_MODEL", "css-model")
    monkeypatch.setenv("GROQ_SEO_MODEL", "seo-model")
    monkeypatch.setenv("GROQ_QA_MODEL", "qa-model")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("UNKNOWN_SETTING", "ignored")

    settings = Settings()
    manager_groq = settings.require_groq_configuration(GroqAgentName.MANAGER)
    html_groq = settings.require_groq_configuration(GroqAgentName.HTML)
    css_groq = settings.require_groq_configuration(GroqAgentName.CSS)
    seo_groq = settings.require_groq_configuration(GroqAgentName.SEO)
    qa_groq = settings.require_groq_configuration(GroqAgentName.QA)

    assert settings.groq_manager_api_key == SecretStr("manager-secret")
    assert settings.groq_html_api_key == SecretStr("html-secret")
    assert settings.groq_css_api_key == SecretStr("css-secret")
    assert settings.groq_seo_api_key == SecretStr("seo-secret")
    assert settings.groq_qa_api_key == SecretStr("qa-secret")
    assert manager_groq.api_key == "manager-secret"
    assert html_groq.api_key == "html-secret"
    assert css_groq.api_key == "css-secret"
    assert seo_groq.api_key == "seo-secret"
    assert qa_groq.api_key == "qa-secret"
    assert manager_groq.model == "manager-model"
    assert html_groq.model == "html-model"
    assert css_groq.model == "css-model"
    assert seo_groq.model == "seo-model"
    assert qa_groq.model == "qa-model"
    assert settings.groq_model_for(GroqAgentName.MANAGER) == "manager-model"
    assert settings.groq_model_for(GroqAgentName.HTML) == "html-model"
    assert settings.groq_model_for(GroqAgentName.CSS) == "css-model"
    assert settings.groq_model_for(GroqAgentName.SEO) == "seo-model"
    assert settings.groq_model_for(GroqAgentName.QA) == "qa-model"
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
    monkeypatch.setenv("GROQ_MANAGER_API_KEY", "manager-super-secret-key")
    monkeypatch.setenv("GROQ_HTML_API_KEY", "html-super-secret-key")
    monkeypatch.setenv("GROQ_CSS_API_KEY", "css-super-secret-key")
    monkeypatch.setenv("GROQ_SEO_API_KEY", "seo-super-secret-key")
    monkeypatch.setenv("GROQ_QA_API_KEY", "qa-super-secret-key")
    monkeypatch.delenv("GROQ_CSS_MODEL", raising=False)

    settings = Settings()

    assert "manager-super-secret-key" not in repr(settings)
    assert "html-super-secret-key" not in repr(settings)
    assert "css-super-secret-key" not in repr(settings)
    assert "seo-super-secret-key" not in repr(settings)
    assert "qa-super-secret-key" not in repr(settings)
    assert "manager-super-secret-key" not in str(settings.model_dump(mode="json"))
    assert "qa-super-secret-key" not in str(settings.model_dump(mode="json"))
    with pytest.raises(ConfigurationError) as error:
        settings.require_groq_configuration(GroqAgentName.CSS)
    assert "css-super-secret-key" not in str(error.value)


def test_qa_groq_configuration_is_role_specific(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_QA_API_KEY", "qa-secret")
    monkeypatch.setenv("GROQ_QA_MODEL", "qa-model")

    settings = Settings()
    qa_groq = settings.require_groq_configuration(GroqAgentName.QA)

    assert qa_groq.api_key == "qa-secret"
    assert qa_groq.model == "qa-model"
    assert settings.groq_model_for(GroqAgentName.QA) == "qa-model"


def test_qa_groq_configuration_requires_qa_key_and_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_MANAGER_API_KEY", "manager-secret")
    monkeypatch.setenv("GROQ_MANAGER_MODEL", "manager-model")
    monkeypatch.delenv("GROQ_QA_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_QA_MODEL", raising=False)

    with pytest.raises(ConfigurationError) as error:
        Settings().require_groq_configuration(GroqAgentName.QA)

    assert "GROQ_QA_API_KEY" in str(error.value)
    assert "GROQ_QA_MODEL" in str(error.value)
    assert "manager-secret" not in str(error.value)


def test_legacy_shared_key_does_not_replace_role_specific_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "legacy-shared-secret")
    monkeypatch.setenv("GROQ_MODEL", "legacy-shared-model")
    monkeypatch.delenv("GROQ_CSS_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_CSS_MODEL", raising=False)

    with pytest.raises(ConfigurationError) as error:
        Settings().require_groq_configuration(GroqAgentName.CSS)

    assert "GROQ_CSS_API_KEY" in str(error.value)
    assert "GROQ_CSS_MODEL" in str(error.value)
    assert "legacy-shared-secret" not in str(error.value)


def test_legacy_shared_key_does_not_replace_qa_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "legacy-shared-secret")
    monkeypatch.setenv("GROQ_MODEL", "legacy-shared-model")
    monkeypatch.delenv("GROQ_QA_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_QA_MODEL", raising=False)

    with pytest.raises(ConfigurationError) as error:
        Settings().require_groq_configuration(GroqAgentName.QA)

    assert "GROQ_QA_API_KEY" in str(error.value)
    assert "GROQ_QA_MODEL" in str(error.value)
    assert "legacy-shared-secret" not in str(error.value)


def test_seo_groq_configuration_is_isolated_and_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_MANAGER_API_KEY", "manager-secret")
    monkeypatch.setenv("GROQ_MANAGER_MODEL", "manager-model")
    monkeypatch.setenv("GROQ_SEO_API_KEY", "seo-secret")
    monkeypatch.setenv("GROQ_SEO_MODEL", "seo-model")

    settings = Settings()
    seo = settings.require_groq_configuration(GroqAgentName.SEO)

    assert seo.api_key == "seo-secret"
    assert seo.model == "seo-model"
    assert "seo-secret" in settings.groq_api_key_values

    monkeypatch.delenv("GROQ_SEO_API_KEY")
    monkeypatch.delenv("GROQ_SEO_MODEL")
    with pytest.raises(ConfigurationError) as error:
        Settings().require_groq_configuration(GroqAgentName.SEO)
    assert "GROQ_SEO_API_KEY" in str(error.value)
    assert "GROQ_SEO_MODEL" in str(error.value)
    assert "manager-secret" not in str(error.value)
