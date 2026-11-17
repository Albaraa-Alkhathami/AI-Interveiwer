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
