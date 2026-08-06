import re
from pathlib import Path

from agentorchestra.scripts.verify_clean_install import REQUIRED_ENV, _mermaid_fences_closed

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def test_required_documentation_exists_and_readme_links_resolve():
    expected = {"setup.md", "usage.md", "architecture.md", "troubleshooting.md", "demo.md"}
    assert {path.name for path in DOCS[1:]} == expected
    readme = DOCS[0].read_text(encoding="utf-8")
    links = re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", readme)
    for link in links:
        target = link.split("#", 1)[0]
        if target and "://" not in target:
            assert (ROOT / target).exists(), link


def test_documentation_matches_scope_and_evidence_boundaries():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    assert "fixed sample site" in combined
    assert "screenshots do not influence qa" in combined.lower()
    assert "Lighthouse SEO" in combined
    assert "no javascript" in combined.lower()
    assert "critical recovery" in combined.lower()
    assert all(_mermaid_fences_closed(path.read_text(encoding="utf-8")) for path in DOCS)
    assert not re.search(r"(?:[A-Za-z]:\\Users\\|/Users/|/home/)[^\s)`]+", combined)
    assert not re.search(r"\bgsk_[A-Za-z0-9]{20,}\b", combined)
    assert "PowerPoint" not in combined
    assert "speaker notes" not in combined.lower()


def test_environment_example_and_documented_scripts_are_complete():
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    names = {
        line.split("=", 1)[0].lstrip("# ").strip()
        for line in env_text.splitlines()
        if "=" in line
    }
    assert names >= REQUIRED_ENV
    for script in (
        "run_demo.py",
        "verify_clean_install.py",
        "run_edit_flow.py",
        "run_specialist.py",
        "run_lighthouse_seo.py",
        "capture_page_screenshot.py",
        "reset_demo_site.py",
        "run_ui.py",
    ):
        assert (ROOT / "scripts" / script).is_file()
