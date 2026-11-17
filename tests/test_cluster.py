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
