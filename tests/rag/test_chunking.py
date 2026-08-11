from __future__ import annotations

from nanobot.rag.chunking import (
    DeterministicChunker,
    EmbeddingInputBuilder,
    chunking_signature,
)
from nanobot.rag.config import RagChunkingConfig
from nanobot.rag.parser import ParsedBlock
from nanobot.rag.types import SourceKind, SourceLocation


class WordTokenizer:
    version = "word-tokenizer-v1"

    def encode(self, text: str) -> tuple[str, ...]:
        return tuple(text.split())

    def decode(self, token_ids: tuple[str, ...]) -> str:
        return " ".join(token_ids)


def _heading(text: str, path: tuple[str, ...]) -> ParsedBlock:
    return ParsedBlock(
        text=text,
        location=SourceLocation(kind=SourceKind.HEADING, heading_path=path),
    )


def test_chunker_prefers_structure_boundaries_and_merges_same_heading() -> None:
    chunker = DeterministicChunker(
        RagChunkingConfig(target_tokens=8, overlap_tokens=2, max_sequence_tokens=16),
        WordTokenizer(),
    )
    blocks = (
        _heading("install package", ("Setup",)),
        _heading("run command", ("Setup",)),
        _heading("verify output", ("Verify",)),
    )

    chunks = chunker.chunk(blocks)

    assert [chunk.text for chunk in chunks] == [
        "install package\n\nrun command",
        "verify output",
    ]
    assert chunks[0].location.heading_path == ("Setup",)
    assert chunks[1].location.heading_path == ("Verify",)
    assert [chunk.ordinal for chunk in chunks] == [0, 1]


def test_oversized_block_uses_deterministic_overlapping_token_windows() -> None:
    chunker = DeterministicChunker(
        RagChunkingConfig(target_tokens=4, overlap_tokens=1, max_sequence_tokens=8),
        WordTokenizer(),
    )
    block = ParsedBlock(
        text="zero one two three four five six seven eight nine",
        location=SourceLocation(kind=SourceKind.PDF_PAGE, page=3),
    )

    chunks = chunker.chunk((block,))

    assert [chunk.text for chunk in chunks] == [
        "zero one two three",
        "three four five six",
        "six seven eight nine",
    ]
    assert all(chunk.location.page == 3 for chunk in chunks)
    assert all(chunk.token_count == 4 for chunk in chunks)


def test_page_and_slide_boundaries_never_merge() -> None:
    chunker = DeterministicChunker(
        RagChunkingConfig(target_tokens=10, overlap_tokens=2, max_sequence_tokens=16),
        WordTokenizer(),
    )
    blocks = (
        ParsedBlock("page one", SourceLocation(kind=SourceKind.PDF_PAGE, page=1)),
        ParsedBlock("page two", SourceLocation(kind=SourceKind.PDF_PAGE, page=2)),
        ParsedBlock("slide one", SourceLocation(kind=SourceKind.SLIDE, slide=1)),
        ParsedBlock("slide two", SourceLocation(kind=SourceKind.SLIDE, slide=2)),
    )

    chunks = chunker.chunk(blocks)

    assert [chunk.text for chunk in chunks] == [
        "page one",
        "page two",
        "slide one",
        "slide two",
    ]


def test_text_and_spreadsheet_ranges_are_merged_without_crossing_sheet() -> None:
    chunker = DeterministicChunker(
        RagChunkingConfig(target_tokens=10, overlap_tokens=2, max_sequence_tokens=16),
        WordTokenizer(),
    )
    blocks = (
        ParsedBlock(
            "row one",
            SourceLocation(
                kind=SourceKind.SPREADSHEET_ROWS,
                sheet="A",
                row_start=1,
                row_end=1,
            ),
        ),
        ParsedBlock(
            "row two",
            SourceLocation(
                kind=SourceKind.SPREADSHEET_ROWS,
                sheet="A",
                row_start=2,
                row_end=2,
            ),
        ),
        ParsedBlock(
            "other sheet",
            SourceLocation(
                kind=SourceKind.SPREADSHEET_ROWS,
                sheet="B",
                row_start=1,
                row_end=1,
            ),
        ),
    )

    chunks = chunker.chunk(blocks)

    assert len(chunks) == 2
    assert chunks[0].location.sheet == "A"
    assert chunks[0].location.row_start == 1
    assert chunks[0].location.row_end == 2
    assert chunks[1].location.sheet == "B"


def test_e5_inputs_include_prefix_and_never_exceed_model_limit() -> None:
    tokenizer = WordTokenizer()
    builder = EmbeddingInputBuilder(tokenizer, max_sequence_tokens=12)
    location = SourceLocation(
        kind=SourceKind.TEXT_LINES,
        line_start=10,
        line_end=20,
    )

    passage = builder.passage(
        "one two three four five six seven eight nine ten",
        filename="guide.md",
        location=location,
    )
    query = builder.query("one two three four five six seven eight nine ten eleven twelve")

    assert passage.startswith("passage:")
    assert "guide.md" in passage
    assert query.startswith("query:")
    assert len(tokenizer.encode(passage)) <= 12
    assert len(tokenizer.encode(query)) <= 12


def test_chunking_signature_changes_with_tokenizer_or_config() -> None:
    base = RagChunkingConfig()
    tokenizer = WordTokenizer()

    first = chunking_signature(base, tokenizer)
    second = chunking_signature(base.model_copy(update={"target_tokens": 360}), tokenizer)

    assert len(first) == 64
    assert first != second
    assert first == chunking_signature(base, tokenizer)
