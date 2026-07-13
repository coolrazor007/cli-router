"""Condensed formatting for streamed tool output."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI escape sequences (colors, cursor moves, spinners)."""
    return _ANSI_RE.sub("", text)


def first_meaningful_line(text: str | None) -> str:
    """First non-blank, de-noised line of a stage's output — a one-line teaser.

    Skips Markdown heading markers and blank lines so the teaser is the first
    real sentence, not ``##`` or empty space.
    """
    if not text:
        return ""
    for raw in text.splitlines():
        line = strip_ansi(raw).strip().lstrip("#").strip()
        if line:
            return line
    return ""


def condense_extracted(text: str | None, *, max_lines: int = 12, max_chars: int = 900) -> str:
    """Trim a stage's extracted output to a half-page preview.

    Keeps at most ``max_lines`` non-blank lines and ``max_chars`` characters,
    collapsing runs of blank lines, and appends a truncation marker when content
    was dropped so the reader knows the full text lives in the artifacts.
    """
    if not text:
        return ""
    lines: list[str] = []
    blank_pending = False
    truncated = False
    for raw in text.splitlines():
        line = strip_ansi(raw).rstrip()
        if not line.strip():
            blank_pending = bool(lines)
            continue
        if len(lines) >= max_lines:
            truncated = True
            break
        if blank_pending:
            lines.append("")
            blank_pending = False
        lines.append(line)
    preview = "\n".join(lines)
    if len(preview) > max_chars:
        preview = preview[:max_chars].rstrip()
        truncated = True
    if truncated:
        preview = f"{preview}\n…"
    return preview


@dataclass
class _DiffFile:
    path: str
    added: int = 0
    removed: int = 0
    line_index: int = 0


class OutputCondenser:
    """Stateful line condenser for TUI workflow output."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._thinking_index: int | None = None
        self._thinking_count = 0
        self._in_tagged_thinking = False
        self._in_diff = False
        self._diff_file: _DiffFile | None = None

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    def feed(self, line: str) -> None:
        text = line.rstrip("\n")
        stripped = text.strip()
        if not stripped:
            return

        if self._consume_thinking(stripped):
            return
        if self._consume_diff(text):
            return

        self._lines.append(text)

    def _consume_thinking(self, stripped: str) -> bool:
        lower = stripped.lower()
        if self._in_tagged_thinking or lower.startswith("<thinking>"):
            if lower.startswith("</thinking>"):
                self._in_tagged_thinking = False
                return True
            self._in_tagged_thinking = "</thinking>" not in lower
            self._increment_thinking()
            return True
        if lower.startswith("thinking") or lower.startswith("[thinking]") or stripped.startswith("\U0001f4ad"):
            self._increment_thinking()
            return True
        return False

    def _increment_thinking(self) -> None:
        self._thinking_count += 1
        rendered = f"thinking... ({self._thinking_count} lines hidden)"
        if self._thinking_index is None:
            self._thinking_index = len(self._lines)
            self._lines.append(rendered)
        else:
            self._lines[self._thinking_index] = rendered

    def _consume_diff(self, text: str) -> bool:
        if text.startswith("diff --git "):
            self._in_diff = True
            path = _path_from_diff_header(text) or "unknown file"
            self._start_diff_file(path)
            return True
        if not self._in_diff:
            return False

        if text.startswith("+++ "):
            path = _path_from_marker(text, "+++ ")
            if path and (self._diff_file is None or self._diff_file.path != path):
                self._start_diff_file(path)
            return True
        if text.startswith("--- ") or text.startswith("@@"):
            return True
        if self._diff_file is not None and text.startswith("+"):
            self._diff_file.added += 1
            self._update_diff_line()
            return True
        if self._diff_file is not None and text.startswith("-"):
            self._diff_file.removed += 1
            self._update_diff_line()
            return True
        if text.startswith(" "):
            return True
        self._in_diff = False
        self._diff_file = None
        return False

    def _start_diff_file(self, path: str) -> None:
        self._diff_file = _DiffFile(path=path, line_index=len(self._lines))
        self._lines.append(_render_diff_file(self._diff_file))

    def _update_diff_line(self) -> None:
        if self._diff_file is not None:
            self._lines[self._diff_file.line_index] = _render_diff_file(self._diff_file)


def _render_diff_file(diff_file: _DiffFile) -> str:
    return f"edited {diff_file.path} (+{diff_file.added} -{diff_file.removed})"


def _path_from_diff_header(text: str) -> str | None:
    match = re.match(r"diff --git a/(.+?) b/(.+)$", text)
    if not match:
        return None
    return match.group(2)


def _path_from_marker(text: str, prefix: str) -> str | None:
    path = text[len(prefix) :].strip()
    if path == "/dev/null":
        return None
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path or None
