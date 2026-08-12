from __future__ import annotations

import json
from pathlib import Path

from nanobot.rag.evaluation import (
    EvaluationCase,
    EvaluationCorpus,
    EvaluationHit,
    RetrievalEvaluationRunner,
    calibrate_threshold,
    load_evaluation_corpus,
    load_evaluation_results,
)


def _corpus() -> EvaluationCorpus:
    return EvaluationCorpus(
        version="mixed-zh-en-v1",
        cases=(
            EvaluationCase("q1", "如何安装？", ("d1",), True),
            EvaluationCase("q2", "What is code ABC_123?", ("d2",), True),
            EvaluationCase("q3", "没有答案的问题", (), False),
        ),
    )


def test_versioned_chinese_english_corpus_round_trips_from_json(tmp_path: Path) -> None:
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps(
            {
                "version": "mixed-zh-en-v1",
                "cases": [
                    {"id": "q1", "query": "如何安装？", "relevant_document_ids": ["d1"], "answerable": True},
                    {"id": "q2", "query": "What is ABC_123?", "relevant_document_ids": [], "answerable": False},
                ],
            },
            ensure_ascii=False,
        )
    )

    corpus = load_evaluation_corpus(path)

    assert corpus.version == "mixed-zh-en-v1"
    assert corpus.cases[0].query == "如何安装？"
    assert corpus.cases[1].answerable is False


def test_committed_mixed_language_release_fixture_is_versioned() -> None:
    corpus = load_evaluation_corpus(
        Path(__file__).parent / "fixtures" / "retrieval" / "mixed_zh_en_v1.json"
    )

    assert corpus.version == "mixed-zh-en-v1"
    assert any("ABC_123" in case.query for case in corpus.cases)
    assert any(not case.answerable for case in corpus.cases)


def test_evaluation_runner_compares_modes_and_calculates_release_metrics() -> None:
    outputs = {
        ("hybrid", "q1"): (EvaluationHit("d1", 0.9, True),),
        ("hybrid", "q2"): (EvaluationHit("d2", 0.8, True),),
        ("hybrid", "q3"): (),
        ("lexical", "q1"): (EvaluationHit("wrong", 0.7, False),),
        ("lexical", "q2"): (EvaluationHit("d2", 0.8, True),),
        ("lexical", "q3"): (EvaluationHit("wrong", 0.6, False),),
        ("dense", "q1"): (EvaluationHit("d1", 0.9, True),),
        ("dense", "q2"): (),
        ("dense", "q3"): (),
    }
    runner = RetrievalEvaluationRunner(
        lambda mode, case: outputs[(mode, case.case_id)]
    )

    report = runner.run(_corpus())

    hybrid = report.mode("hybrid")
    assert hybrid.recall_at_30 == 1.0
    assert hybrid.evidence_success_rate == 1.0
    assert hybrid.no_answer_false_positive_rate == 0.0
    assert hybrid.citation_accuracy == 1.0
    assert hybrid.cross_principal_hits == 0
    assert hybrid.release_thresholds_passed is True
    assert report.mode("hybrid").composite_score >= max(
        report.mode("lexical").composite_score,
        report.mode("dense").composite_score,
    )
    assert report.release_passed is True


def test_release_gate_fails_when_hybrid_is_below_a_single_path_baseline() -> None:
    outputs = {
        (mode, case.case_id): (
            (EvaluationHit(case.relevant_document_ids[0], 0.9, True),)
            if case.answerable and mode == "lexical"
            else ()
        )
        for mode in ("lexical", "dense", "hybrid")
        for case in _corpus().cases
    }

    report = RetrievalEvaluationRunner(
        lambda mode, case: outputs[(mode, case.case_id)]
    ).run(_corpus())

    assert report.mode("lexical").release_thresholds_passed is True
    assert report.release_passed is False


def test_threshold_calibration_maximizes_f1_subject_to_false_positive_cap() -> None:
    samples = (
        (0.95, True),
        (0.80, True),
        (0.70, True),
        (0.65, False),
        (0.20, False),
        (0.10, False),
    )

    result = calibrate_threshold(samples, max_false_positive_rate=0.1)

    assert result.threshold == 0.70
    assert result.false_positive_rate == 0.0
    assert result.f1 == 1.0


def test_fixed_release_profile_results_pass_all_quality_and_baseline_gates() -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "retrieval"
    corpus = load_evaluation_corpus(fixture_root / "mixed_zh_en_v1.json")
    results = load_evaluation_results(
        fixture_root / "mixed_zh_en_v1_results.json",
        expected_corpus_version=corpus.version,
    )

    report = RetrievalEvaluationRunner(
        lambda mode, case: results[(mode, case.case_id)]
    ).run(corpus)

    assert report.release_passed is True
    assert report.mode("hybrid").recall_at_30 == 1.0
    assert report.mode("hybrid").evidence_success_rate == 1.0
    assert report.mode("hybrid").no_answer_false_positive_rate == 0.0
    assert report.mode("hybrid").citation_accuracy == 1.0
    assert report.mode("hybrid").cross_principal_hits == 0
