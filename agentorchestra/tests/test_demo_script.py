from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agentorchestra.scripts import run_demo
from tests.test_edit_flow_cli import FakeFlow, report
from tests.test_workspace_service import make_settings

ROOT = Path(__file__).resolve().parents[1]


def ready(_settings):
    return {"configuration": True, "runtime": True, "workspace": True}


def test_demo_check_is_boolean_only_and_never_calls_flow(tmp_path, capsys):
    settings = make_settings(tmp_path)
    flow = FakeFlow(report())
    before = settings.working_site_dir.joinpath("index.html").read_bytes()

    code = run_demo.main(
        ["--check"], settings=settings, flow=flow, readiness_checker=ready
    )

    output = capsys.readouterr().out
    assert code == 0
    assert flow.calls == []
    assert "overall: ready" in output
    assert "gsk_" not in output
    assert settings.working_site_dir.joinpath("index.html").read_bytes() == before


def test_real_demo_readiness_process_has_clean_stderr_and_exits():
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_demo.py"), "--check"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        stdout, stderr = process.communicate()
        raise AssertionError("Readiness subprocess did not shut down cleanly.") from exc

    assert process.poll() is not None
    assert process.returncode in {0, 1}
    assert "demo readiness:" in stdout
    assert "overall:" in stdout
    assert stderr == ""


def test_demo_run_requires_apply_before_flow_or_reset(tmp_path, capsys):
    settings = make_settings(tmp_path)
    flow = FakeFlow(report())
    resets = []

    code = run_demo.main(
        ["--run", "--reset-first"],
        settings=settings,
        flow=flow,
        readiness_checker=ready,
        resetter=lambda **kwargs: resets.append(kwargs),
    )

    assert code == 2
    assert flow.calls == []
    assert resets == []
    assert "--apply" in capsys.readouterr().out


def test_demo_run_reuses_flow_and_transactional_resets(tmp_path, capsys):
    settings = make_settings(tmp_path)
    flow = FakeFlow(report("accepted"))
    resets = []

    code = run_demo.main(
        [
            "--run",
            "--apply",
            "--target-page",
            "index.html",
            "--instruction",
            "Apply edit.",
            "--reset-first",
            "--reset-after",
        ],
        settings=settings,
        flow=flow,
        readiness_checker=ready,
        resetter=lambda **kwargs: resets.append(kwargs),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert len(flow.calls) == 1
    assert len(resets) == 2
    assert "flow outcome: accepted" in output
    assert "reset first: complete" in output
    assert "reset after: complete" in output
