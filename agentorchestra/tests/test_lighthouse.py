import json
import os
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agentorchestra.seo_models import LighthouseRunStatus
from agentorchestra.services.lighthouse import (
    normalize_lighthouse_seo_report,
    run_lighthouse_seo,
)
from agentorchestra.services.workspace import create_staged_copy
from tests.test_workspace_service import make_settings


def payload():
    return {
        "categories": {
            "seo": {
                "score": 0.91,
                "auditRefs": [
                    {"id": "meta-description"},
                    {"id": "document-title"},
                    {"id": "manual-check"},
                ],
            },
            "performance": {"score": 0.01, "auditRefs": [{"id": "speed-index"}]},
        },
        "audits": {
            "meta-description": {
                "title": "Document has a meta description",
                "score": 0,
                "scoreDisplayMode": "binary",
            },
            "document-title": {
                "title": "Document has a title",
                "score": 1,
                "scoreDisplayMode": "binary",
            },
            "manual-check": {
                "title": "Manual SEO check",
                "score": None,
                "scoreDisplayMode": "manual",
            },
            "speed-index": {
                "title": "Speed Index",
                "score": 0,
                "scoreDisplayMode": "numeric",
            },
        },
    }


@contextmanager
def fake_preview(_root):
    yield "http://127.0.0.1:43210"


def test_lighthouse_uses_safe_project_local_seo_only_command_and_normalizes(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "lh-run")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        output = next(
            item.split("=", 1)[1] for item in command if item.startswith("--output-path=")
        )
        with open(output, "w", encoding="utf-8") as report:
            json.dump(payload(), report)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = run_lighthouse_seo(
        handle,
        "index.html",
        settings=settings,
        subprocess_runner=runner,
        preview_factory=fake_preview,
        report_id_factory=lambda: "report",
        clock=iter([1.0, 1.02]).__next__,
    )

    command, kwargs = calls[0]
    assert os.path.basename(command[0]).lower() in {"npx", "npx.cmd"}
    assert command[1:3] == ["--no-install", "lighthouse"]
    assert "http://127.0.0.1:43210/index.html" in command
    assert "--only-categories=seo" in command
    assert not any(
        category in " ".join(command) for category in ["performance", "accessibility", "pwa"]
    )
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 120
    assert kwargs["cwd"] == settings.project_root
    assert result.status is LighthouseRunStatus.SUCCEEDED
    assert result.score == 91
    assert result.failed_audit_ids == ["meta-description"]
    assert (settings.project_root / result.report_path).is_file()
    assert [item.audit_id for item in result.audits] == [
        "document-title",
        "manual-check",
        "meta-description",
    ]
    assert "speed-index" not in result.model_dump_json()


@pytest.mark.parametrize(
    "failure", ["missing", "timeout", "nonzero", "missing-report", "malformed", "no-seo"]
)
def test_lighthouse_returns_safe_structured_failures(tmp_path, failure):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: f"lh-{failure}")

    def runner(command, **kwargs):
        if failure == "missing":
            raise FileNotFoundError
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if failure == "nonzero":
            return SimpleNamespace(returncode=1, stdout="secret", stderr="C:\\private\\secret")
        if failure == "missing-report":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        output = next(
            item.split("=", 1)[1] for item in command if item.startswith("--output-path=")
        )
        with open(output, "w", encoding="utf-8") as report:
            if failure == "malformed":
                report.write("not json")
            else:
                json.dump({"categories": {}, "audits": {}}, report)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = run_lighthouse_seo(
        handle,
        "index.html",
        settings=settings,
        subprocess_runner=runner,
        preview_factory=fake_preview,
        report_id_factory=lambda: "report",
        clock=iter([1.0, 1.01]).__next__,
    )

    assert result.status is LighthouseRunStatus.FAILED
    assert result.score is None
    assert result.audits == []
    assert "private" not in result.model_dump_json()
    assert list(settings.lighthouse_report_dir.glob("seo-*.json")) == []


def test_lighthouse_removes_partial_report_after_timeout(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "lh-partial")

    def runner(command, **kwargs):
        output = next(
            item.split("=", 1)[1] for item in command if item.startswith("--output-path=")
        )
        with open(output, "w", encoding="utf-8") as report:
            report.write("partial")
        raise subprocess.TimeoutExpired(command, 1)

    result = run_lighthouse_seo(
        handle,
        "index.html",
        settings=settings,
        subprocess_runner=runner,
        preview_factory=fake_preview,
        report_id_factory=lambda: "report",
        clock=iter([1.0, 1.01]).__next__,
    )

    assert result.status is LighthouseRunStatus.FAILED
    assert list(settings.lighthouse_report_dir.glob("seo-*.json")) == []


def test_lighthouse_rejects_and_removes_report_when_process_exits_nonzero(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "lh-cleanup-success")

    def runner(command, **kwargs):
        output = next(
            item.split("=", 1)[1] for item in command if item.startswith("--output-path=")
        )
        with open(output, "w", encoding="utf-8") as report:
            json.dump(payload(), report)
        return SimpleNamespace(returncode=1, stdout="", stderr="Runtime error encountered: EPERM")

    result = run_lighthouse_seo(
        handle,
        "index.html",
        settings=settings,
        subprocess_runner=runner,
        preview_factory=fake_preview,
        report_id_factory=lambda: "report",
        clock=iter([1.0, 1.02]).__next__,
    )

    assert result.status is LighthouseRunStatus.FAILED
    assert result.score is None
    assert list(settings.lighthouse_report_dir.glob("seo-*.json")) == []


def test_lighthouse_always_exits_preview_context_on_subprocess_failure(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "lh-cleanup")
    lifecycle = []

    @contextmanager
    def preview(_root):
        lifecycle.append("started")
        try:
            yield "http://127.0.0.1:43210"
        finally:
            lifecycle.append("stopped")

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    result = run_lighthouse_seo(
        handle,
        "index.html",
        settings=settings,
        subprocess_runner=runner,
        preview_factory=preview,
        report_id_factory=lambda: "report",
        clock=iter([1.0, 1.01]).__next__,
    )

    assert result.status is LighthouseRunStatus.FAILED
    assert lifecycle == ["started", "stopped"]


def test_normalizer_rejects_missing_or_invalid_seo_category():
    with pytest.raises((KeyError, ValueError)):
        normalize_lighthouse_seo_report(
            {"categories": {}, "audits": {}},
            run_id="run",
            target_page="index.html",
            report_path="reports/lighthouse/report.json",
            latency_ms=1.0,
        )
