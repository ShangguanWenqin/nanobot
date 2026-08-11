from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from nanobot.rag.config import RagParsingConfig
from nanobot.rag.parser import ParseCompleteness, RagParseError, parse_document
from nanobot.rag.types import RagErrorCode, SourceKind


def _write_pdf(path: Path, page_texts: list[str], *, password: str | None = None) -> None:
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        content = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    if password:
        writer.encrypt(password)
    with path.open("wb") as stream:
        writer.write(stream)


def test_pdf_parser_preserves_one_based_page_locations(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    _write_pdf(path, ["First page", "Second page"])

    result = parse_document(path, RagParsingConfig())

    assert [block.text for block in result.blocks] == ["First page", "Second page"]
    assert [block.location.kind for block in result.blocks] == [
        SourceKind.PDF_PAGE,
        SourceKind.PDF_PAGE,
    ]
    assert [block.location.page for block in result.blocks] == [1, 2]
    assert result.metrics.total_pages == 2
    assert result.metrics.processed_pages == 2
    assert result.completeness is ParseCompleteness.COMPLETE


def test_pdf_page_limit_is_reported_as_truncation(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    _write_pdf(path, ["One", "Two"])

    result = parse_document(path, RagParsingConfig(max_pdf_pages=1))

    assert [block.text for block in result.blocks] == ["One"]
    assert result.completeness is ParseCompleteness.TRUNCATED
    assert result.limit_reason == "max_pdf_pages"
    assert result.metrics.total_pages == 2
    assert result.metrics.processed_pages == 1


def test_pdf_without_extractable_text_is_not_treated_as_ocr(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _write_pdf(path, [""])

    with pytest.raises(RagParseError) as exc_info:
        parse_document(path, RagParsingConfig())

    assert exc_info.value.code is RagErrorCode.NO_EXTRACTABLE_TEXT


def test_encrypted_pdf_is_rejected_with_specific_error(tmp_path: Path) -> None:
    path = tmp_path / "secret.pdf"
    _write_pdf(path, ["Secret"], password="password")

    with pytest.raises(RagParseError) as exc_info:
        parse_document(path, RagParsingConfig())

    assert exc_info.value.code is RagErrorCode.ENCRYPTED_DOCUMENT


def test_pdf_content_stream_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "stream.pdf"
    _write_pdf(path, ["Long enough"])

    with pytest.raises(RagParseError) as exc_info:
        parse_document(
            path,
            RagParsingConfig(max_pdf_content_stream_bytes=8),
        )

    assert exc_info.value.code is RagErrorCode.UNSAFE_DOCUMENT
