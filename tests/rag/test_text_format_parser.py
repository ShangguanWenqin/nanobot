from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.rag.config import RagParsingConfig
from nanobot.rag.parser import DocumentFormat, RagParseError, parse_document
from nanobot.rag.types import RagErrorCode, SourceKind


@pytest.mark.parametrize(
    ("filename", "content", "expected_format"),
    [
        ("a.txt", "普通文本", DocumentFormat.TEXT),
        ("a.md", "# 标题", DocumentFormat.MARKDOWN),
        ("a.csv", "name,value\na,1", DocumentFormat.CSV),
        ("a.json", '{"name": "nanobot"}', DocumentFormat.JSON),
        ("a.xml", "<root><name>nanobot</name></root>", DocumentFormat.XML),
        ("a.html", "<!doctype html><p>nanobot</p>", DocumentFormat.HTML),
        ("a.log", "INFO ready", DocumentFormat.LOG),
        ("a.yaml", "name: nanobot", DocumentFormat.YAML),
        ("a.yml", "name: nanobot", DocumentFormat.YAML),
        ("a.toml", 'name = "nanobot"', DocumentFormat.TOML),
        ("a.ini", "[app]\nname=nanobot", DocumentFormat.INI),
        ("a.cfg", "name=nanobot", DocumentFormat.CFG),
    ],
)
def test_each_supported_text_extension_has_a_fixture(
    tmp_path: Path,
    filename: str,
    content: str,
    expected_format: DocumentFormat,
) -> None:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    result = parse_document(path, RagParsingConfig())

    assert result.document_format is expected_format
    assert result.blocks
    assert all(block.location.kind is SourceKind.TEXT_LINES for block in result.blocks)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "latin-1"])
def test_text_decoding_is_deterministic_for_supported_encodings(
    tmp_path: Path, encoding: str
) -> None:
    path = tmp_path / "encoded.txt"
    path.write_bytes("café".encode(encoding))

    result = parse_document(path, RagParsingConfig())

    assert result.blocks[0].text == "café"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("bad.json", "{not-json}"),
        ("bad.xml", "<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><x>&e;</x>"),
        ("bad.html", "plain text with an html suffix"),
        ("bad.toml", "key = [unterminated"),
    ],
)
def test_structured_text_content_is_validated(
    tmp_path: Path, filename: str, content: str
) -> None:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RagParseError) as exc_info:
        parse_document(path, RagParsingConfig())

    assert exc_info.value.code in {
        RagErrorCode.UNSUPPORTED_FORMAT,
        RagErrorCode.UNSAFE_DOCUMENT,
    }


def test_known_binary_signature_cannot_be_renamed_to_text(tmp_path: Path) -> None:
    path = tmp_path / "renamed.txt"
    path.write_bytes(b"%PDF-1.7\nprintable header")

    with pytest.raises(RagParseError) as exc_info:
        parse_document(path, RagParsingConfig())

    assert exc_info.value.code is RagErrorCode.UNSUPPORTED_FORMAT
