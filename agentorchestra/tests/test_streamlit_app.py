from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "src" / "agentorchestra" / "ui" / "app.py"


def test_streamlit_import_and_unconfirmed_run_make_no_execution():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    assert app.title[0].value == "AgentOrchestra — Webpage Editor"
    run_button = next(button for button in app.button if button.label == "Run edit")
    run_button.click().run()
    assert any("Confirm the execution notice" in item.value for item in app.error)


def test_streamlit_unconfirmed_reset_is_guarded():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    reset = next(button for button in app.button if button.label == "Reset demo site")
    reset.click().run()
    assert any("Confirm the reset" in item.value for item in app.error)
