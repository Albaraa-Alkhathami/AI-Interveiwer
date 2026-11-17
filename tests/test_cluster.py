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


def test_a_repeated_opening_is_stripped_before_comparison():
    stock = "Before choosing a direction I would make the trade-off explicit."
    answers = [
        f"{stock} I would then compare the two options on cost and on reversibility.",
        f"{stock} I would then ask which of the constraints is real and which is habit.",
        "A mutex guards shared state; a semaphore counts permits.",
    ]
    bodies, info = cd.strip_stock_openings(answers, min_count=2)
    assert info["stripped"] == 2
    assert info["stock_openings"] == 1
    assert all(stock not in b for b in bodies), "the template opening survived"
    assert bodies[0] != bodies[1], "bodies must still be compared, not collapsed"
    assert bodies[2] == answers[2]


def test_a_unique_opening_is_kept():
    answers = [
        "I once rebuilt a queue consumer under load.",
        "A mutex guards shared state; a semaphore counts permits.",
    ]
    bodies, info = cd.strip_stock_openings(answers, min_count=2)
    assert info["stripped"] == 0
    assert bodies == answers


def test_opening_stats_counts_rows_sharing_a_template():
    stock = "Before choosing a direction I would make the trade-off explicit."
    answers = [f"{stock} body one here.", f"{stock} body two here.", "A unique opening line."]
    stats = cd.opening_stats(answers, min_count=2)
    assert stats["rows_with_stock_opening"] == 2
    assert stats["stock_openings"] == 1
    assert stats["largest"][0] == 2


def test_a_short_body_is_not_stripped():
    stock = "Before choosing a direction I would make the trade-off explicit."
    answers = [f"{stock} ok.", f"{stock} ok.", f"{stock} ok."]
    bodies, info = cd.strip_stock_openings(answers, min_count=2)
    assert info["stripped"] == 0, "stripping a short answer would leave nothing to compare"
    assert bodies == answers
