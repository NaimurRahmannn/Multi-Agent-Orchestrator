from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser

from agentorchestra.models import SpecialistName

_NETWORK_OR_ACTIVE_CSS_REFERENCE = re.compile(
    r"(?is)@import\b|expression\s*\(|url\s*\(\s*(['\"]?)(.*?)\1\s*\)"
)
_UNSAFE_CSS_SCHEME = re.compile(r"(?i)^(?:https?|file|ftp|javascript|vbscript|data):")
_URI_ATTRIBUTES = {"action", "formaction", "href", "poster", "src", "xlink:href"}
_ACTIVE_HTML_TAGS = {"embed", "iframe", "object", "script"}

Event = tuple[object, ...]


class _OwnershipHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.non_seo_events: list[Event] = []
        self.metadata_events: list[Event] = []
        self.heading_events: list[Event] = []
        self.presentation_events: list[Event] = []
        self.active_events: list[Event] = []
        self._title_depth = 0
        self._script_depth = 0
        self._style_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_start(tag, attrs, self_closing=False)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._record_start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        event = ("end", normalized_tag)
        if self._title_depth:
            self.metadata_events.append(event)
            if normalized_tag == "title":
                self._title_depth -= 1
            return
        if self._script_depth:
            self.active_events.append(event)
            if normalized_tag == "script":
                self._script_depth -= 1
        if self._style_depth:
            self.presentation_events.append(event)
            if normalized_tag == "style":
                self._style_depth -= 1
        if _is_heading(normalized_tag):
            self.heading_events.append(event)
            self.non_seo_events.append(("end", "heading"))
        else:
            self.non_seo_events.append(event)

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.metadata_events.append(("data", data))
            return
        if self._script_depth:
            self.active_events.append(("data", data))
        if self._style_depth:
            self.presentation_events.append(("data", data))
        if data.strip():
            self.non_seo_events.append(("data", data))

    def handle_entityref(self, name: str) -> None:
        self._record_text_event(("entity", name))

    def handle_charref(self, name: str) -> None:
        self._record_text_event(("charref", name))

    def handle_comment(self, data: str) -> None:
        self.non_seo_events.append(("comment", data))

    def handle_decl(self, decl: str) -> None:
        self.non_seo_events.append(("decl", decl.casefold()))

    def handle_pi(self, data: str) -> None:
        self.non_seo_events.append(("pi", data))

    def _record_start(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        self_closing: bool,
    ) -> None:
        normalized_tag = tag.casefold()
        normalized_attrs = tuple(
            sorted((name.casefold(), value or "") for name, value in attrs)
        )
        event = ("startend" if self_closing else "start", normalized_tag, normalized_attrs)

        if normalized_tag == "title":
            self.metadata_events.append(event)
            if not self_closing:
                self._title_depth += 1
            return
        if normalized_tag == "meta" and _is_seo_meta(normalized_attrs):
            self.metadata_events.append(event)
            return

        if normalized_tag in _ACTIVE_HTML_TAGS:
            self.active_events.append(event)
        if normalized_tag == "script" and not self_closing:
            self._script_depth += 1

        active_attributes = tuple(
            (name, value)
            for name, value in normalized_attrs
            if name == "srcdoc"
            or name.startswith("on")
            or (name in _URI_ATTRIBUTES and value.lstrip().casefold().startswith("javascript:"))
        )
        if active_attributes:
            self.active_events.append(("attributes", normalized_tag, active_attributes))

        if normalized_tag == "style":
            self.presentation_events.append(event)
            if not self_closing:
                self._style_depth += 1
        style_attributes = tuple(
            (name, value) for name, value in normalized_attrs if name == "style"
        )
        if style_attributes:
            self.presentation_events.append(
                ("attributes", normalized_tag, style_attributes)
            )
        if normalized_tag == "link" and _is_stylesheet_link(normalized_attrs):
            self.presentation_events.append(event)

        if _is_heading(normalized_tag):
            self.heading_events.append(event)
            self.non_seo_events.append(
                (event[0], "heading", normalized_attrs)
            )
        else:
            self.non_seo_events.append(event)

    def _record_text_event(self, event: Event) -> None:
        if self._title_depth:
            self.metadata_events.append(event)
            return
        if self._script_depth:
            self.active_events.append(event)
        if self._style_depth:
            self.presentation_events.append(event)
        self.non_seo_events.append(event)


def validate_specialist_patch(
    before: str,
    after: str,
    specialist: SpecialistName,
) -> str | None:
    """Return a safe rejection message when a patch crosses trusted ownership boundaries."""
    if specialist is SpecialistName.CSS:
        if Counter(_unsafe_css_references(after)) - Counter(_unsafe_css_references(before)):
            return "CSS patches may not introduce imports or active/external URL references."
        return None

    before_policy = _parse_html_policy(before)
    after_policy = _parse_html_policy(after)
    if Counter(after_policy.active_events) - Counter(before_policy.active_events):
        return "HTML patches may not introduce or change active scripting content."

    if specialist is SpecialistName.HTML:
        if before_policy.metadata_events != after_policy.metadata_events:
            return "HTML patches may not change SEO-owned title or metadata content."
        if before_policy.presentation_events != after_policy.presentation_events:
            return "HTML patches may not change inline or linked presentation content."
        return None

    if specialist is SpecialistName.SEO:
        if before_policy.non_seo_events != after_policy.non_seo_events:
            return "SEO patches may change only title, supported metadata, or heading levels."
        if (
            before_policy.metadata_events == after_policy.metadata_events
            and before_policy.heading_events == after_policy.heading_events
        ):
            return "SEO patches must contain a supported metadata or heading-level change."
        return None

    return "Patch specialist is outside the supported ownership policy."


def _parse_html_policy(content: str) -> _OwnershipHTMLParser:
    parser = _OwnershipHTMLParser()
    parser.feed(content)
    parser.close()
    return parser


def _is_heading(tag: str) -> bool:
    return len(tag) == 2 and tag[0] == "h" and tag[1] in "123456"


def _is_seo_meta(attrs: tuple[tuple[str, str], ...]) -> bool:
    values = dict(attrs)
    return values.get("name", "").casefold() == "description" or values.get(
        "property", ""
    ).casefold().startswith("og:")


def _is_stylesheet_link(attrs: tuple[tuple[str, str], ...]) -> bool:
    values = dict(attrs)
    return "stylesheet" in values.get("rel", "").casefold().split()


def _unsafe_css_references(content: str) -> list[str]:
    unsafe: list[str] = []
    for match in _NETWORK_OR_ACTIVE_CSS_REFERENCE.finditer(content):
        token = match.group(0)
        if token.lstrip().casefold().startswith(("@import", "expression")):
            unsafe.append(token)
            continue
        reference = (match.group(2) or "").strip()
        if reference.startswith("//") or _UNSAFE_CSS_SCHEME.match(reference):
            unsafe.append(token)
    return unsafe
