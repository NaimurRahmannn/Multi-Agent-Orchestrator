from agentorchestra.scripts import run_demo
from tests.test_edit_flow_cli import FakeFlow, report
from tests.test_workspace_service import make_settings


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
