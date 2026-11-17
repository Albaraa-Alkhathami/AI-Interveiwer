"""The duplicate-clustering helper: pure numpy, no model or network needed."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import cluster_duplicates as cd  # noqa: E402


def unit(*v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


def test_identical_vectors_form_one_cluster():
    v = unit([1, 0, 0], [1, 0, 0], [0, 1, 0])
    groups = cd.pair_clusters(v, 0.9)
    assert groups == [[0, 1]]


def test_distinct_vectors_are_not_clustered():
    v = unit([1, 0, 0], [0, 1, 0], [0, 0, 1])
    assert cd.pair_clusters(v, 0.9) == []


def test_threshold_decides_what_counts_as_duplicate():
    v = unit([1, 0, 0], [0.8, 0.6, 0])          # cosine 0.8
    assert cd.pair_clusters(v, 0.75) == [[0, 1]]
    assert cd.pair_clusters(v, 0.85) == []


def test_transitive_chain_merges_into_one_group():
    v = unit([1, 0, 0], [0.8, 0.6, 0], [0.28, 0.96, 0])
    groups = cd.pair_clusters(v, 0.7)
    assert len(groups) == 1 and sorted(groups[0]) == [0, 1, 2]


def test_recurring_sentences_are_removed_wherever_they_sit():
    stock = "The first approach generally emphasizes one set of trade-offs."
    answers = [
        f"{stock} I would profile the render loop and cut draw calls per frame.",
        f"{stock} I would shard the search index by tenant and cache the hottest shard.",
        "A mutex guards shared state; a semaphore counts permits under load.",
    ]
    bodies, info = cd.strip_template_sentences(answers, min_count=2)
    assert info["stock_sentences"] == 1
    assert all(stock not in b for b in bodies)
    assert bodies[0] != bodies[1], "the specific halves must survive and stay distinct"
    assert info["rows_with_specific_text"] == 3


def test_a_fully_templated_answer_is_not_compared():
    stock = "The first approach generally emphasizes one set of trade-offs."
    answers = [
        f"{stock} The choice depends on requirements such as performance and complexity.",
        f"{stock} The choice depends on requirements such as performance and complexity.",
        f"{stock} I would profile the render loop and cut draw calls per frame.",
    ]
    bodies, info = cd.strip_template_sentences(answers, min_count=2)
    assert info["rows_fully_templated"] == 2, info
    assert info["keep"] == [2], "only the answer with specific text is comparable"
    assert len(bodies) == len(answers), "index alignment must be preserved"


def test_a_unique_answer_is_untouched():
    answers = [
        "I once rebuilt a queue consumer under load and cut the retry storm.",
        "A mutex guards shared state; a semaphore counts permits under load.",
    ]
    bodies, info = cd.strip_template_sentences(answers, min_count=2)
    assert bodies == answers
    assert info["rows_fully_templated"] == 0


def test_slot_variants_share_one_sentence_key():
    a = "With Kafka Streams already in place, I would keep the stack we have."
    b = "With Apache Flink already in place, I would keep the stack we have."
    assert cd.sentence_key(a) != cd.sentence_key(b)
    assert cd.sentence_key(a, slot=True) == cd.sentence_key(b, slot=True)


def test_step_numbers_are_a_slot_not_content():
    a = "(2) agree the success measure and the stop conditions up front."
    b = "(3) agree the success measure and the stop conditions up front."
    assert cd.sentence_key(a, slot=True) == cd.sentence_key(b, slot=True)


def test_slot_key_keeps_different_advice_apart():
    a = "I would shard the index by tenant so writes stay local."
    b = "A mutex guards shared state when two threads write at once."
    assert cd.sentence_key(a, slot=True) != cd.sentence_key(b, slot=True)


def test_slot_min_count_makes_slot_filled_rows_fully_templated():
    stock = ("With {} already in place, that means working with the stack we have "
             "before proposing anything new.")
    texts = [stock.format(name) for name in ("Kafka", "Flink", "RabbitMQ", "SQS", "Redis")]

    _, verbatim_only = cd.strip_template_sentences(texts, 50)
    assert verbatim_only["rows_fully_templated"] == 0, "each variant is unique verbatim"

    _, slot_aware = cd.strip_template_sentences(texts, 50, slot_min_count=2)
    assert slot_aware["slot_sentences"] >= 1
    assert slot_aware["rows_fully_templated"] == len(texts)


def test_subject_slot_merges_subject_variants_in_any_case():
    a = "Then I would choose an appropriate angular technique, in small steps."
    b = "Then I would choose an appropriate vue.js technique, in small steps."
    assert (cd.sentence_key(a, slot=True, subject="Angular")
            == cd.sentence_key(b, slot=True, subject="Vue.js"))
    assert cd.sentence_key(a, slot=True, subject="Angular") != cd.sentence_key(a, slot=True, subject="Java")


def test_short_subjects_are_not_used_as_slots():
    key = cd.sentence_key("I would go home after the fix.", slot=True, subject="Go")
    assert "<s>" not in key, "a two-letter subject must not rewrite ordinary prose"


def test_subject_slot_rows_become_fully_templated():
    stock = "Then I would choose an appropriate {} technique, implement it in small steps."
    names = ("angular", "react", "vue", "svelte", "ember", "backbone")
    texts = [stock.format(n) for n in names]
    subjects = [n.capitalize() for n in names]

    _, before = cd.strip_template_sentences(texts, 50, slot_min_count=2)
    assert before["rows_fully_templated"] == 0, "lowercase subject slots are not <x>"

    _, after = cd.strip_template_sentences(texts, 50, slot_min_count=2, subjects=subjects)
    assert after["rows_fully_templated"] == len(texts)
