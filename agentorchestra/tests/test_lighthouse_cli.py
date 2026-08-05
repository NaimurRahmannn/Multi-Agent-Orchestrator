from agentorchestra.scripts import run_lighthouse_seo
from agentorchestra.seo_models import LighthouseAuditItem, LighthouseSEOResult
from tests.test_workspace_service import make_settings


def test_lighthouse_cli_requires_apply_before_execution(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    calls = []
    monkeypatch.setattr(
        run_lighthouse_seo,
        "run_working_lighthouse_seo",
        lambda *args, **kwargs: calls.append(1),
    )

    code = run_lighthouse_seo.main(["--target-page", "index.html"], settings=settings)

    assert code == 2
    assert calls == []
    assert "--apply" in capsys.readouterr().out


def test_lighthouse_cli_prints_normalized_result(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(
        run_lighthouse_seo,
        "run_working_lighthouse_seo",
        lambda *args, **kwargs: LighthouseSEOResult(
            status="succeeded",
            run_id="working-test",
            target_page="index.html",
            score=88,
            audits=[
                LighthouseAuditItem(
                    audit_id="document-title",
                    title="Document has a title",
                    status="passed",
                    score=100,
                )
            ],
            failed_audit_ids=[],
            report_path="reports/lighthouse/seo-working-test.json",
            latency_ms=3.0,
        ),
    )

    code = run_lighthouse_seo.main(["--target-page", "index.html", "--apply"], settings=settings)

    output = capsys.readouterr().out
    assert code == 0
    assert "seo score: 88" in output
    assert "failed audits: none" in output
