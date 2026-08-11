"""Deterministic structure-aware chunking and E5 input construction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nanobot.rag.config import RagChunkingConfig
from nanobot.rag.parser import ParsedBlock
from nanobot.rag.types import SourceKind, SourceLocation


class TokenCodec(Protocol):
    version: str

    def encode(self, text: str) -> Sequence[Any]: ...

    def decode(self, token_ids: Sequence[Any]) -> str: ...


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    text: str
    token_count: int
    location: SourceLocation


class DeterministicChunker:
    def __init__(self, config: RagChunkingConfig, tokenizer: TokenCodec) -> None:
        self.config = config
        self.tokenizer = tokenizer

    def chunk(self, blocks: Sequence[ParsedBlock]) -> tuple[ChunkDraft, ...]:
        drafts: list[ChunkDraft] = []
        buffered: list[ParsedBlock] = []

        def flush() -> None:
            if not buffered:
                return
            text = "\n\n".join(block.text for block in buffered)
            drafts.append(
                ChunkDraft(
                    ordinal=len(drafts),
                    text=text,
                    token_count=len(self.tokenizer.encode(text)),
                    location=_merge_locations(tuple(block.location for block in buffered)),
                )
            )
            buffered.clear()

        for block in blocks:
            token_ids = tuple(self.tokenizer.encode(block.text))
            if len(token_ids) > self.config.target_tokens:
                flush()
                for window in self._windows(token_ids):
                    drafts.append(
                        ChunkDraft(
                            ordinal=len(drafts),
                            text=self.tokenizer.decode(window),
                            token_count=len(window),
                            location=block.location,
                        )
                    )
                continue
            if buffered:
                candidate = "\n\n".join(
                    [*(item.text for item in buffered), block.text]
                )
                if (
                    not _same_section(buffered[-1].location, block.location)
                    or len(self.tokenizer.encode(candidate)) > self.config.target_tokens
                ):
                    flush()
            buffered.append(block)
        flush()
        return tuple(drafts)

    def _windows(self, token_ids: tuple[Any, ...]) -> tuple[tuple[Any, ...], ...]:
        target = self.config.target_tokens
        step = target - self.config.overlap_tokens
        windows: list[tuple[Any, ...]] = []
        start = 0
        while start < len(token_ids):
            end = min(len(token_ids), start + target)
            window = token_ids[start:end]
            if window:
                windows.append(window)
            if end == len(token_ids):
                break
            start += step
        return tuple(windows)


class EmbeddingInputBuilder:
    def __init__(self, tokenizer: TokenCodec, *, max_sequence_tokens: int) -> None:
        if max_sequence_tokens < 2:
            raise ValueError("embedding sequence limit must be at least two tokens")
        self.tokenizer = tokenizer
        self.max_sequence_tokens = max_sequence_tokens

    def passage(
        self,
        text: str,
        *,
        filename: str,
        location: SourceLocation,
    ) -> str:
        context = f"file {filename} location {_format_location(location)}"
        return self._bounded("passage:", context, text)

    def query(self, text: str) -> str:
        return self._bounded("query:", "", text)

    def _bounded(self, prefix: str, context: str, body: str) -> str:
        fixed = " ".join(part for part in (prefix, context) if part)
        complete = f"{fixed} {body}".strip()
        if len(self.tokenizer.encode(complete)) <= self.max_sequence_tokens:
            return complete
        fixed_ids = tuple(self.tokenizer.encode(fixed))
        if len(fixed_ids) >= self.max_sequence_tokens:
            return self.tokenizer.decode(fixed_ids[: self.max_sequence_tokens])
        body_ids = tuple(self.tokenizer.encode(body))
        remaining = self.max_sequence_tokens - len(fixed_ids)
        bounded_body = self.tokenizer.decode(body_ids[:remaining])
        result = f"{fixed} {bounded_body}".strip()
        while len(self.tokenizer.encode(result)) > self.max_sequence_tokens and remaining > 0:
            remaining -= 1
            bounded_body = self.tokenizer.decode(body_ids[:remaining])
            result = f"{fixed} {bounded_body}".strip()
        return result


def chunking_signature(config: RagChunkingConfig, tokenizer: TokenCodec) -> str:
    payload = {
        "chunking_version": config.version,
        "max_sequence_tokens": config.max_sequence_tokens,
        "overlap_tokens": config.overlap_tokens,
        "target_tokens": config.target_tokens,
        "tokenizer_version": tokenizer.version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _same_section(left: SourceLocation, right: SourceLocation) -> bool:
    if left.kind is not right.kind:
        return False
    if left.kind is SourceKind.HEADING:
        return left.heading_path == right.heading_path
    if left.kind is SourceKind.PDF_PAGE:
        return left.page == right.page
    if left.kind is SourceKind.SLIDE:
        return left.slide == right.slide
    if left.kind is SourceKind.SPREADSHEET_ROWS:
        return left.sheet == right.sheet
    return True


def _merge_locations(locations: tuple[SourceLocation, ...]) -> SourceLocation:
    first = locations[0]
    last = locations[-1]
    if first.kind is SourceKind.SPREADSHEET_ROWS:
        return SourceLocation(
            kind=SourceKind.SPREADSHEET_ROWS,
            sheet=first.sheet,
            row_start=first.row_start,
            row_end=last.row_end,
        )
    if first.kind is SourceKind.TEXT_LINES:
        return SourceLocation(
            kind=SourceKind.TEXT_LINES,
            line_start=first.line_start,
            line_end=last.line_end,
        )
    return first


def _format_location(location: SourceLocation) -> str:
    if location.kind is SourceKind.PDF_PAGE:
        return f"page {location.page}"
    if location.kind is SourceKind.HEADING:
        return "heading " + " > ".join(location.heading_path)
    if location.kind is SourceKind.SLIDE:
        return f"slide {location.slide}"
    if location.kind is SourceKind.SPREADSHEET_ROWS:
        return f"sheet {location.sheet} rows {location.row_start}-{location.row_end}"
    if location.kind is SourceKind.TEXT_LINES:
        return f"lines {location.line_start}-{location.line_end}"
    return "unknown"


__all__ = [
    "ChunkDraft",
    "DeterministicChunker",
    "EmbeddingInputBuilder",
    "TokenCodec",
    "chunking_signature",
]
