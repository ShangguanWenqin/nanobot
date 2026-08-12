"""Versioned retrieval evaluation, baseline comparison, and threshold calibration."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

EvaluationMode = Literal["lexical", "dense", "hybrid"]


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    query: str
    relevant_document_ids: tuple[str, ...]
    answerable: bool

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.query.strip():
            raise ValueError("evaluation case ID and query must not be empty")
        if self.answerable != bool(self.relevant_document_ids):
            raise ValueError("answerable cases must declare relevant documents")


@dataclass(frozen=True, slots=True)
class EvaluationCorpus:
    version: str
    cases: tuple[EvaluationCase, ...]

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.cases:
            raise ValueError("evaluation corpus requires a version and cases")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("evaluation case IDs must be unique")


@dataclass(frozen=True, slots=True)
class EvaluationHit:
    document_id: str
    score: float
    citation_correct: bool
    cross_principal: bool = False


@dataclass(frozen=True, slots=True)
class ModeEvaluation:
    mode: EvaluationMode
    recall_at_30: float
    evidence_success_rate: float
    no_answer_false_positive_rate: float
    citation_accuracy: float
    cross_principal_hits: int

    @property
    def composite_score(self) -> float:
        return (
            self.recall_at_30
            + self.evidence_success_rate
            + (1.0 - self.no_answer_false_positive_rate)
            + self.citation_accuracy
        ) / 4.0

    @property
    def release_thresholds_passed(self) -> bool:
        return (
            self.recall_at_30 >= 0.90
            and self.evidence_success_rate >= 0.80
            and self.no_answer_false_positive_rate <= 0.10
            and self.citation_accuracy >= 0.95
            and self.cross_principal_hits == 0
        )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    corpus_version: str
    modes: tuple[ModeEvaluation, ...]

    def mode(self, mode: EvaluationMode) -> ModeEvaluation:
        for item in self.modes:
            if item.mode == mode:
                return item
        raise KeyError(mode)

    @property
    def release_passed(self) -> bool:
        hybrid = self.mode("hybrid")
        baseline = max(
            self.mode("lexical").composite_score,
            self.mode("dense").composite_score,
        )
        return hybrid.release_thresholds_passed and hybrid.composite_score >= baseline


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    threshold: float
    f1: float
    false_positive_rate: float


EvaluationSearch = Callable[[EvaluationMode, EvaluationCase], Sequence[EvaluationHit]]


class RetrievalEvaluationRunner:
    def __init__(self, search: EvaluationSearch) -> None:
        self.search = search

    def run(self, corpus: EvaluationCorpus) -> EvaluationReport:
        return EvaluationReport(
            corpus_version=corpus.version,
            modes=tuple(self._run_mode(mode, corpus) for mode in ("lexical", "dense", "hybrid")),
        )

    def _run_mode(self, mode: EvaluationMode, corpus: EvaluationCorpus) -> ModeEvaluation:
        answerable = [case for case in corpus.cases if case.answerable]
        unanswerable = [case for case in corpus.cases if not case.answerable]
        recalled = 0
        evidence_success = 0
        false_positives = 0
        cited = 0
        citation_total = 0
        cross_principal = 0
        for case in corpus.cases:
            hits = tuple(self.search(mode, case))
            cross_principal += sum(hit.cross_principal for hit in hits)
            relevant = [hit for hit in hits if hit.document_id in case.relevant_document_ids]
            if case.answerable:
                if any(hit.document_id in case.relevant_document_ids for hit in hits[:30]):
                    recalled += 1
                if any(hit.document_id in case.relevant_document_ids for hit in hits[:6]):
                    evidence_success += 1
                citation_total += len(relevant[:6])
                cited += sum(hit.citation_correct for hit in relevant[:6])
            elif hits:
                false_positives += 1
        return ModeEvaluation(
            mode=mode,
            recall_at_30=_ratio(recalled, len(answerable)),
            evidence_success_rate=_ratio(evidence_success, len(answerable)),
            no_answer_false_positive_rate=_ratio(false_positives, len(unanswerable)),
            citation_accuracy=_ratio(cited, citation_total),
            cross_principal_hits=cross_principal,
        )


def calibrate_threshold(
    samples: Sequence[tuple[float, bool]],
    *,
    max_false_positive_rate: float,
) -> CalibrationResult:
    if not samples:
        raise ValueError("threshold calibration requires samples")
    thresholds = sorted({score for score, _ in samples}, reverse=True)
    negatives = sum(not relevant for _, relevant in samples)
    best: CalibrationResult | None = None
    for threshold in thresholds:
        true_positive = sum(score >= threshold and relevant for score, relevant in samples)
        false_positive = sum(score >= threshold and not relevant for score, relevant in samples)
        false_negative = sum(score < threshold and relevant for score, relevant in samples)
        false_positive_rate = _ratio(false_positive, negatives)
        if false_positive_rate > max_false_positive_rate:
            continue
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        result = CalibrationResult(threshold, f1, false_positive_rate)
        if best is None or (result.f1, -result.threshold) > (best.f1, -best.threshold):
            best = result
    if best is None:
        raise ValueError("no threshold satisfies the false-positive constraint")
    return best


def load_evaluation_corpus(path: str | Path) -> EvaluationCorpus:
    raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("evaluation corpus must be a JSON object")
    payload = cast(dict[str, object], raw)
    version = payload.get("version")
    cases = payload.get("cases")
    if not isinstance(version, str) or not isinstance(cases, list):
        raise ValueError("evaluation corpus has invalid version or cases")
    parsed: list[EvaluationCase] = []
    for raw_case in cast(list[object], cases):
        if not isinstance(raw_case, dict):
            raise ValueError("evaluation case must be an object")
        item = cast(dict[str, object], raw_case)
        case_id = item.get("id")
        query = item.get("query")
        relevant = item.get("relevant_document_ids")
        answerable = item.get("answerable")
        if not isinstance(relevant, list):
            raise ValueError("evaluation case has invalid fields")
        relevant_values = cast(list[object], relevant)
        if (
            not isinstance(case_id, str)
            or not isinstance(query, str)
            or not all(isinstance(value, str) for value in relevant_values)
            or not isinstance(answerable, bool)
        ):
            raise ValueError("evaluation case has invalid fields")
        parsed.append(
            EvaluationCase(
                case_id,
                query,
                tuple(cast(list[str], relevant_values)),
                answerable,
            )
        )
    return EvaluationCorpus(version, tuple(parsed))


def load_evaluation_results(
    path: str | Path,
    *,
    expected_corpus_version: str,
) -> dict[tuple[EvaluationMode, str], tuple[EvaluationHit, ...]]:
    raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("evaluation results must be a JSON object")
    payload = cast(dict[str, object], raw)
    if payload.get("corpus_version") != expected_corpus_version:
        raise ValueError("evaluation result corpus version does not match")
    raw_results = payload.get("results")
    if not isinstance(raw_results, dict):
        raise ValueError("evaluation results have no mode mapping")
    results: dict[tuple[EvaluationMode, str], tuple[EvaluationHit, ...]] = {}
    mode_mapping = cast(dict[str, object], raw_results)
    for mode in ("lexical", "dense", "hybrid"):
        raw_cases = mode_mapping.get(mode)
        if not isinstance(raw_cases, dict):
            raise ValueError(f"evaluation results have no {mode} cases")
        for case_id, raw_hits in cast(dict[str, object], raw_cases).items():
            if not isinstance(raw_hits, list):
                raise ValueError("evaluation case hits must be an array")
            hits: list[EvaluationHit] = []
            for raw_hit in cast(list[object], raw_hits):
                if not isinstance(raw_hit, dict):
                    raise ValueError("evaluation hit must be an object")
                item = cast(dict[str, object], raw_hit)
                document_id = item.get("document_id")
                score = item.get("score")
                citation_correct = item.get("citation_correct")
                cross_principal = item.get("cross_principal", False)
                if (
                    not isinstance(document_id, str)
                    or not isinstance(score, (int, float))
                    or not isinstance(citation_correct, bool)
                    or not isinstance(cross_principal, bool)
                ):
                    raise ValueError("evaluation hit has invalid fields")
                hits.append(
                    EvaluationHit(
                        document_id,
                        float(score),
                        citation_correct,
                        cross_principal,
                    )
                )
            results[(mode, case_id)] = tuple(hits)
    return results


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


__all__ = [
    "CalibrationResult",
    "EvaluationCase",
    "EvaluationCorpus",
    "EvaluationHit",
    "EvaluationReport",
    "ModeEvaluation",
    "RetrievalEvaluationRunner",
    "calibrate_threshold",
    "load_evaluation_corpus",
    "load_evaluation_results",
]
