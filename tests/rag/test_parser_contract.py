from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.rag.config import RagParsingConfig
from nanobot.rag.parser import (
    DocumentFormat,
    ParseCompleteness,
    RagParseError,
    detect_document_format,
    parse_document,
)
from nanobot.rag.types import RagErrorCode, SourceKind
from nanobot.utils.document import reference_non_image_attachments


def _config(**overrides: object) -> RagParsingConfig:
    return RagParsingConfig.model_validate(overrides)


def test_text_parser_returns_structured_line_locations(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    path.write_text("# 安装\n\n第一步。\n第二步。\n\n## 验证\n\n运行测试。\n", encoding="utf-8")

    result = parse_document(path, _config())

    assert result.document_format is DocumentFormat.MARKDOWN
    assert result.completeness is ParseCompleteness.COMPLETE
    assert result.total_chars > 0
    assert [block.text for block in result.blocks] == [
        "# 安装",
        "第一步。\n第二步。",
        "## 验证",
        "运行测试。",
    ]
    assert result.blocks[1].location.kind is SourceKind.TEXT_LINES
    assert result.blocks[1].location.line_start == 3
    assert result.blocks[1].location.line_end == 4


def test_truncation_is_explicit_and_never_reported_as_complete(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_text("0123456789\n" * 20, encoding="utf-8")

    result = parse_document(path, _config(max_extracted_chars=35))

    assert result.completeness is ParseCompleteness.TRUNCATED
    assert result.limit_reason == "max_extracted_chars"
    assert result.total_chars == 35
    assert sum(len(block.text) for block in result.blocks) <= 35


def test_empty_document_has_a_stable_safe_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("  \n\t\n", encoding="utf-8")

    with pytest.raises(RagParseError) as exc_info:
        parse_document(path, _config())

    assert exc_info.value.code is RagErrorCode.NO_EXTRACTABLE_TEXT
    assert str(path) not in exc_info.value.safe_message


def test_binary_file_cannot_bypass_detection_by_using_txt_suffix(tmp_path: Path) -> None:
    path = tmp_path / "payload.txt"
    path.write_bytes(b"\x00\x01\x02\x03binary")

    with pytest.raises(RagParseError) as exc_info:
        detect_document_format(path)

    assert exc_info.value.code is RagErrorCode.UNSUPPORTED_FORMAT


def test_pdf_suffix_requires_pdf_signature(tmp_path: Path) -> None:
    path = tmp_path / "renamed.pdf"
    path.write_text("not a PDF", encoding="utf-8")

    with pytest.raises(RagParseError) as exc_info:
        detect_document_format(path)

    assert exc_info.value.code is RagErrorCode.UNSUPPORTED_FORMAT


def test_missing_file_reports_safe_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"

    with pytest.raises(RagParseError) as exc_info:
        parse_document(path, _config())

    assert exc_info.value.code is RagErrorCode.UNSAFE_DOCUMENT
    assert str(path) not in exc_info.value.safe_message


def test_existing_current_turn_attachment_behavior_remains_a_reference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("内容不应由附件适配层提前展开", encoding="utf-8")

    content, images = reference_non_image_attachments("请阅读", [str(path)])

    assert content.startswith("请阅读\n\n[Attachment: ")
    assert "内容不应由附件适配层提前展开" not in content
    assert images == []
