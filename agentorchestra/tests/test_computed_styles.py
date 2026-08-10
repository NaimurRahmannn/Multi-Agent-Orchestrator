from contextlib import contextmanager, nullcontext

from agentorchestra.services.computed_styles import verify_computed_style_evidence
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.style_models import StyleChangeEvidence
from tests.test_workspace_service import make_settings


class _Page:
    def route(self, pattern, handler):
        del pattern, handler

    def goto(self, url, **kwargs):
        del url, kwargs

    def wait_for_load_state(self, state, **kwargs):
        del state, kwargs

    def evaluate(self, script, values):
        del script, values
        return {
            "actualAfter": "rgb(220, 38, 38)",
            "expectedBefore": "rgb(22, 163, 74)",
            "expectedAfter": "rgb(220, 38, 38)",
        }

    def close(self):
        return None


class _Context:
    def __init__(self):
        self.page = _Page()

    def new_page(self):
        return self.page

    def close(self):
        return None


class _Browser:
    def __init__(self):
        self.context = _Context()

    def new_context(self):
        return self.context

    def close(self):
        return None


class _Chromium:
    def __init__(self, executable_path):
        self.executable_path = str(executable_path)

    def launch(self, *, headless):
        assert headless is True
        return _Browser()


class _Playwright:
    def __init__(self, executable_path):
        self.chromium = _Chromium(executable_path)


def test_browser_computed_style_is_attached_to_semantic_evidence(tmp_path):
    settings = make_settings(tmp_path)
    workspace = create_staged_copy(settings=settings, run_id_factory=lambda: "computed-style")
    executable = tmp_path / "chromium"
    executable.write_text("fake", encoding="utf-8")

    @contextmanager
    def preview_factory(site_root):
        assert site_root == workspace.path
        yield "http://127.0.0.1:43210"

    changes = verify_computed_style_evidence(
        settings=settings,
        site_root=workspace.path,
        target_page="index.html",
        run_id=workspace.run_id,
        changes=[
            StyleChangeEvidence(
                target_id="index.hero.project_cta",
                label="Start a project button",
                selector=".button-link",
                property_name="background",
                before_value="green",
                after_value="#dc2626",
                expected_relation="equals_requested",
            )
        ],
        playwright_factory=lambda: nullcontext(_Playwright(executable)),
        preview_factory=preview_factory,
    )

    assert changes[0].computed_before_value == "rgb(22, 163, 74)"
    assert changes[0].computed_after_value == "rgb(220, 38, 38)"
    assert changes[0].computed_verified is True
