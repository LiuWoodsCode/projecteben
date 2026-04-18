from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextDocument, QFont


LINE_LIMIT = 98
ALLOWED_DUPLICATES = {
    "dtoverlay",
    "dtparam",
    "include",
    "initramfs",
    "hdmi_timings",
    "device_tree_param",
    "device_tree_overlay",
}

KEY_PATTERN = re.compile(r"[A-Za-z0-9_,-]+")


class TokenKind(str, Enum):
    COMMENT = "comment"
    CONDITIONAL = "conditional"
    INCLUDE_KEYWORD = "include_keyword"
    INCLUDE_PATH = "include_path"
    KEY = "key"
    ASSIGN = "assign"
    VALUE = "value"
    INVALID = "invalid"


class LineKind(str, Enum):
    BLANK = "blank"
    COMMENT = "comment"
    CONDITIONAL = "conditional"
    INCLUDE = "include"
    ASSIGNMENT = "assignment"
    INVALID = "invalid"


@dataclass(frozen=True)
class Token:
    line_number: int
    column: int
    length: int
    kind: TokenKind
    text: str


@dataclass(frozen=True)
class ParsedLine:
    line_number: int
    raw: str
    stripped: str
    kind: LineKind
    tokens: tuple[Token, ...]
    key: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    lines: tuple[ParsedLine, ...]

    @property
    def tokens(self) -> tuple[Token, ...]:
        flattened: list[Token] = []
        for parsed_line in self.lines:
            flattened.extend(parsed_line.tokens)
        return tuple(flattened)


@dataclass(frozen=True)
class ValidationMessage:
    line_number: int
    severity: str
    text: str


def _token(line_number: int, column: int, text: str, kind: TokenKind) -> Token:
    return Token(line_number, column, len(text), kind, text)


def parse_line(raw_line: str, line_number: int) -> ParsedLine:
    line = raw_line.rstrip("\r")
    stripped = line.strip()

    if not stripped:
        return ParsedLine(line_number, line, stripped, LineKind.BLANK, ())

    leading_ws = len(line) - len(line.lstrip(" \t"))
    stripped_start = leading_ws

    if stripped.startswith("#"):
        comment_text = line[stripped_start:]
        return ParsedLine(
            line_number,
            line,
            stripped,
            LineKind.COMMENT,
            (_token(line_number, stripped_start, comment_text, TokenKind.COMMENT),),
        )

    if stripped.startswith("[") and stripped.endswith("]"):
        conditional_text = line[stripped_start : stripped_start + len(stripped)]
        return ParsedLine(
            line_number,
            line,
            stripped,
            LineKind.CONDITIONAL,
            (_token(line_number, stripped_start, conditional_text, TokenKind.CONDITIONAL),),
        )

    if stripped.startswith("include"):
        include_start = line.find("include", stripped_start)
        if include_start == -1:
            include_start = stripped_start
        tokens: list[Token] = [
            _token(line_number, include_start, "include", TokenKind.INCLUDE_KEYWORD)
        ]
        remainder = stripped[len("include") :]
        if remainder and remainder[0].isspace():
            include_path = remainder.strip()
            if include_path:
                path_start = line.find(include_path, include_start + len("include"))
                if path_start == -1:
                    path_start = include_start + len("include")
                tokens.append(_token(line_number, path_start, include_path, TokenKind.INCLUDE_PATH))
            return ParsedLine(line_number, line, stripped, LineKind.INCLUDE, tuple(tokens))

        invalid = line[stripped_start : stripped_start + len(stripped)]
        return ParsedLine(
            line_number,
            line,
            stripped,
            LineKind.INVALID,
            tuple(tokens + [_token(line_number, stripped_start, invalid, TokenKind.INVALID)]),
        )

    if "=" in stripped:
        eq_index = line.find("=", stripped_start)
        if eq_index == -1:
            eq_index = stripped_start + stripped.find("=")

        key_text = line[stripped_start:eq_index].strip()
        value_text = line[eq_index + 1 :].strip()

        key_start = line.find(key_text, stripped_start, eq_index) if key_text else eq_index
        if key_start == -1:
            key_start = stripped_start

        value_start = line.find(value_text, eq_index + 1) if value_text else eq_index + 1
        if value_start == -1:
            value_start = eq_index + 1

        key_kind = TokenKind.KEY if key_text and KEY_PATTERN.fullmatch(key_text) else TokenKind.INVALID
        tokens = [
            _token(line_number, key_start, key_text if key_text else "", key_kind),
            _token(line_number, eq_index, "=", TokenKind.ASSIGN),
        ]

        if value_text:
            tokens.append(_token(line_number, value_start, value_text, TokenKind.VALUE))

        filtered_tokens = tuple(token for token in tokens if token.length > 0)
        return ParsedLine(
            line_number,
            line,
            stripped,
            LineKind.ASSIGNMENT,
            filtered_tokens,
            key=key_text,
            value=value_text,
        )

    invalid_text = line[stripped_start : stripped_start + len(stripped)]
    return ParsedLine(
        line_number,
        line,
        stripped,
        LineKind.INVALID,
        (_token(line_number, stripped_start, invalid_text, TokenKind.INVALID),),
    )


def parse_config(text: str) -> ParsedDocument:
    lines = tuple(parse_line(raw_line, index) for index, raw_line in enumerate(text.splitlines(), start=1))
    return ParsedDocument(lines)


def validate_document(document: ParsedDocument) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    seen_keys: dict[str, int] = {}

    for parsed_line in document.lines:
        line = parsed_line.raw
        stripped = parsed_line.stripped
        index = parsed_line.line_number

        if len(line) > LINE_LIMIT:
            messages.append(
                ValidationMessage(
                    index,
                    "warning",
                    f"Line exceeds the {LINE_LIMIT}-character firmware limit.",
                )
            )

        if parsed_line.kind in {LineKind.BLANK, LineKind.COMMENT, LineKind.CONDITIONAL}:
            continue

        if parsed_line.kind == LineKind.INCLUDE:
            include_path = stripped[len("include") :].strip()
            if not include_path:
                messages.append(
                    ValidationMessage(index, "error", "Include directive is missing a file name.")
                )
            continue

        if parsed_line.kind == LineKind.INVALID:
            messages.append(
                ValidationMessage(
                    index,
                    "error",
                    "Expected a property=value entry, comment, include, or conditional filter.",
                )
            )
            continue

        key = parsed_line.key.strip() if parsed_line.key is not None else ""
        value = parsed_line.value.strip() if parsed_line.value is not None else ""

        if not key:
            messages.append(ValidationMessage(index, "error", "Missing property name before '='."))
            continue

        if not KEY_PATTERN.fullmatch(key):
            messages.append(
                ValidationMessage(
                    index,
                    "warning",
                    "Property name contains unusual characters for config.txt syntax.",
                )
            )

        if not value:
            messages.append(ValidationMessage(index, "warning", "Property is set with an empty value."))

        previous_line = seen_keys.get(key)
        if previous_line is not None and key not in ALLOWED_DUPLICATES:
            messages.append(
                ValidationMessage(
                    index,
                    "warning",
                    f"'{key}' already appears on line {previous_line}. Later values usually win.",
                )
            )
        else:
            seen_keys[key] = index

    return messages


def validate_config(text: str) -> list[ValidationMessage]:
    return validate_document(parse_config(text))


class ConfigSyntaxHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._formats: dict[TokenKind, QTextCharFormat] = {
            TokenKind.COMMENT: self._make_format("#6B7280", italic=True),
            TokenKind.CONDITIONAL: self._make_format("#A855F7", weight=QFont.Bold),
            TokenKind.INCLUDE_KEYWORD: self._make_format("#2563EB", weight=QFont.Bold),
            TokenKind.INCLUDE_PATH: self._make_format("#1D4ED8"),
            TokenKind.KEY: self._make_format("#0F766E", weight=QFont.Bold),
            TokenKind.ASSIGN: self._make_format("#475569"),
            TokenKind.VALUE: self._make_format("#B45309"),
            TokenKind.INVALID: self._make_format("#DC2626", underline=True),
        }

    def highlightBlock(self, text: str) -> None:
        parsed = parse_line(text, self.currentBlock().blockNumber() + 1)
        for token in parsed.tokens:
            token_format = self._formats.get(token.kind)
            if token_format is None:
                continue
            self.setFormat(token.column, token.length, token_format)

    @staticmethod
    def _make_format(
        color_hex: str,
        *,
        weight: QFont.Weight = QFont.Normal,
        italic: bool = False,
        underline: bool = False,
    ) -> QTextCharFormat:
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color_hex))
        text_format.setFontWeight(weight)
        text_format.setFontItalic(italic)
        if underline:
            text_format.setUnderlineStyle(QTextCharFormat.SingleUnderline)
            text_format.setUnderlineColor(QColor("#DC2626"))
        return text_format