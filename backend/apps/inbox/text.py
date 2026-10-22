"""
Plain text from a Gmail message payload (M10). Pure functions, no I/O.

Only text parts are read; attachments (any part with a filename) are ignored. The reply is cut at
the first quoted-history marker or signature delimiter, so earlier reminders quoted back to us are
not stored or classified as the customer's words.
"""

import base64
import binascii
import html
import re
from html.parser import HTMLParser
from typing import Any

QUOTE_MARKERS = [
    re.compile(r"^\s*On\b.{0,300}\bwrote:\s*$", re.IGNORECASE),           # Gmail, Apple Mail
    re.compile(r"^\s*Le\b.{0,300}\ba écrit\s*:\s*$", re.IGNORECASE),       # French clients
    re.compile(r"^\s*Am\b.{0,300}\bschrieb\b.*:\s*$", re.IGNORECASE),      # German clients
    re.compile(r"^\s*-{2,}\s*(Original|Forwarded) Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*_{8,}\s*$"),                                           # Outlook separator
    re.compile(r"^\s*From:\s.+$", re.IGNORECASE),                           # Outlook header block
]
# "On Mon, 1 Jan 2026 at 10:00, Name <a@b.c>" + newline + "wrote:" (Gmail wraps long lines).
WRAPPED_ON_WROTE = re.compile(r"^\s*On\b.{0,300}$", re.IGNORECASE)
SIGNATURE_MARKERS = [
    re.compile(r"^--\s*$"),
    re.compile(r"^\s*Sent from my \w+", re.IGNORECASE),
    re.compile(r"^\s*Get Outlook for \w+", re.IGNORECASE),
]
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ‪-‮⁦-⁩﻿]")


def _decode(data: str, charset: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _charset(part: dict[str, Any]) -> str:
    for header in part.get("headers") or []:
        if str(header.get("name", "")).lower() == "content-type":
            match = re.search(r'charset="?([\w.-]+)"?', str(header.get("value", "")), re.IGNORECASE)
            if match:
                return match.group(1)
    return "utf-8"


def _walk(part: dict[str, Any], plain: list[str], rich: list[str], depth: int = 0) -> None:
    if depth > 10:  # malformed or hostile nesting
        return
    mime = str(part.get("mimeType", "")).lower()
    if part.get("filename"):
        return  # attachment
    if mime.startswith("multipart/"):
        for child in part.get("parts") or []:
            _walk(child, plain, rich, depth + 1)
        return
    data = (part.get("body") or {}).get("data")
    if not data:
        return
    if mime == "text/plain":
        plain.append(_decode(data, _charset(part)))
    elif mime == "text/html":
        rich.append(_decode(data, _charset(part)))


class _TextFromHtml(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.quote = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = " ".join(value or "" for name, value in attrs if name == "class")
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag == "blockquote" or "gmail_quote" in classes:
            self.quote += 1
        if tag in self.BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1
        elif tag == "blockquote" and self.quote:
            self.quote -= 1
        if tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip and not self.quote:
            self.out.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextFromHtml()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML falls back to tag stripping
        return html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return "".join(parser.out)


def extract_text(payload: dict[str, Any]) -> str:
    """The message's text: text/plain parts if any, otherwise text/html converted to text."""
    plain: list[str] = []
    rich: list[str] = []
    _walk(payload, plain, rich)
    if plain:
        return "\n".join(plain)
    return "\n".join(html_to_text(part) for part in rich)


def strip_reply(text: str) -> str:
    """The customer's own words: no quoted history, no signature, no control characters."""
    text = CONTROL_CHARS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")
    kept: list[str] = []
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if any(marker.match(line) for marker in QUOTE_MARKERS):
            break
        if WRAPPED_ON_WROTE.match(line) and following.strip().lower().endswith("wrote:"):
            break
        if any(marker.match(line) for marker in SIGNATURE_MARKERS):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line.rstrip())
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def reply_text(payload: dict[str, Any], *, max_chars: int) -> tuple[str, bool]:
    """(stripped reply text capped at max_chars, whether it was truncated)."""
    text = strip_reply(extract_text(payload))
    if len(text) > max_chars:
        return text[:max_chars], True
    return text, False
