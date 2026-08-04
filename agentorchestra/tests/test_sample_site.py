from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "sites" / "fixture"
WORKING_ROOT = PROJECT_ROOT / "sites" / "working"
STAGING_ROOT = PROJECT_ROOT / "sites" / "staging"


class ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stylesheets: list[str] = []
        self.links: list[str] = []
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "link" and attr_map.get("rel") == "stylesheet" and attr_map.get("href"):
            self.stylesheets.append(attr_map["href"])
        if tag == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"])
        if tag == "img" and attr_map.get("src"):
            self.assets.append(attr_map["src"])


def html_pages(root: Path) -> list[Path]:
    return sorted(root.glob("*.html"))


def test_fixture_files_exist():
    expected = {
        FIXTURE_ROOT / "index.html",
        FIXTURE_ROOT / "about.html",
        FIXTURE_ROOT / "contact.html",
        FIXTURE_ROOT / "style.css",
        FIXTURE_ROOT / "assets" / "studio-mark.svg",
    }

    assert all(path.exists() for path in expected)


def test_working_files_initially_match_fixture_files():
    fixture_files = sorted(path.relative_to(FIXTURE_ROOT) for path in FIXTURE_ROOT.rglob("*") if path.is_file())
    working_files = sorted(path.relative_to(WORKING_ROOT) for path in WORKING_ROOT.rglob("*") if path.is_file())

    assert working_files == fixture_files
    for relative_path in fixture_files:
        assert (WORKING_ROOT / relative_path).read_bytes() == (FIXTURE_ROOT / relative_path).read_bytes()


def test_no_javascript_files_exist():
    assert not list((PROJECT_ROOT / "sites").rglob("*.js"))


def test_html_pages_reference_shared_stylesheet():
    for page in html_pages(FIXTURE_ROOT):
        parser = ReferenceParser()
        parser.feed(page.read_text(encoding="utf-8"))
        assert parser.stylesheets == ["style.css"]


def test_local_navigation_targets_exist():
    for page in html_pages(FIXTURE_ROOT):
        parser = ReferenceParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for href in parser.links:
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            assert (FIXTURE_ROOT / href).exists(), f"{page.name} links to missing {href}"


def test_required_asset_references_resolve():
    for page in html_pages(FIXTURE_ROOT):
        parser = ReferenceParser()
        parser.feed(page.read_text(encoding="utf-8"))
        assert parser.assets
        for src in parser.assets:
            assert (FIXTURE_ROOT / src).exists(), f"{page.name} references missing {src}"


def test_staging_contains_no_editable_site_copy_at_initialization():
    files = sorted(path.relative_to(STAGING_ROOT) for path in STAGING_ROOT.rglob("*") if path.is_file())

    assert files == [Path(".gitkeep")]
