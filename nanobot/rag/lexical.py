"""Versioned bilingual lexical analysis and SQLite FTS5 retrieval."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass

from nanobot.rag.store import RagStore

_LEXICAL_RUN = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+(?:[._/-][a-z0-9]+)*")
_GENERATION_ID = re.compile(r"[0-9a-f]{32}")


class BilingualLexicalAnalyzer:
    """Deterministic local analyzer for CJK and identifier-heavy mixed text."""

    version = "nanobot-bilingual-lexical-v1"

    def tokens(self, text: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        tokens: list[str] = []
        seen: set[str] = set()

        def add(token: str) -> None:
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)

        for match in _LEXICAL_RUN.finditer(normalized):
            value = match.group(0)
            if "\u3400" <= value[0] <= "\u9fff":
                add(value)
                for character in value:
                    add(character)
                for index in range(len(value) - 1):
                    add(value[index : index + 2])
                continue
            add(value)
            for separator in (".", "_", "/", "-"):
                if separator in value:
                    for component in value.split(separator):
                        add(component)
        return tuple(tokens)

    def normalize(self, text: str) -> str:
        return " ".join(self.tokens(text))


@dataclass(frozen=True, slots=True)
class LexicalHit:
    chunk_key: int
    document_id: str
    text: str
    location_json: str
    score: float


class LexicalRepository:
    def __init__(self, store: RagStore, analyzer: BilingualLexicalAnalyzer) -> None:
        self.store = store
        self.analyzer = analyzer

    def rebuild_generation(self, generation_id: str) -> None:
        self._validate_generation_id(generation_id)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM chunks_fts")
            rows = connection.execute(
                "SELECT chunk_key, text FROM chunks WHERE generation_id = ? "
                "ORDER BY chunk_key",
                (generation_id,),
            ).fetchall()
            connection.executemany(
                "INSERT INTO chunks_fts(rowid, lexical_text) VALUES (?, ?)",
                (
                    (int(row[0]), self.analyzer.normalize(str(row[1])))
                    for row in rows
                ),
            )
            connection.execute(
                "UPDATE store_manifest SET lexical_analyzer_version = ?, updated_at = ? "
                "WHERE singleton = 1",
                (self.analyzer.version, int(time.time())),
            )

    def search(
        self,
        query: str,
        *,
        generation_id: str,
        limit: int,
    ) -> tuple[LexicalHit, ...]:
        self._validate_generation_id(generation_id)
        if limit < 1:
            raise ValueError("lexical search limit must be positive")
        tokens = self.analyzer.tokens(query)
        if not tokens:
            return ()
        fts_query = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
        )
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT c.chunk_key, c.document_id, c.text, c.location_json, "
                "bm25(chunks_fts) AS lexical_score "
                "FROM chunks_fts "
                "JOIN chunks AS c ON c.chunk_key = chunks_fts.rowid "
                "JOIN documents AS d ON d.document_id = c.document_id "
                "WHERE chunks_fts MATCH ? AND c.generation_id = ? "
                "AND d.status = 'ready' "
                "ORDER BY lexical_score ASC, c.chunk_key ASC LIMIT ?",
                (fts_query, generation_id, limit),
            ).fetchall()
        return tuple(
            LexicalHit(
                chunk_key=int(row[0]),
                document_id=str(row[1]),
                text=str(row[2]),
                location_json=str(row[3]),
                score=float(row[4]),
            )
            for row in rows
        )

    @staticmethod
    def _validate_generation_id(generation_id: str) -> None:
        if not _GENERATION_ID.fullmatch(generation_id):
            raise ValueError("generation_id must be 32 lowercase hexadecimal characters")


__all__ = ["BilingualLexicalAnalyzer", "LexicalHit", "LexicalRepository"]
