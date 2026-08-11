"""Tests for BGE train/eval split helpers."""

from __future__ import annotations

from agent_pochta.services.bge_train_eval import (
    DEFAULT_SPLIT_SEED,
    email_id_hash_bucket,
    is_test_split,
    split_email_ids,
)


def test_email_id_hash_bucket_is_deterministic() -> None:
    first = email_id_hash_bucket("abc-123")
    second = email_id_hash_bucket("abc-123")
    assert first == second
    assert 0.0 <= first < 1.0


def test_email_id_hash_bucket_changes_with_seed() -> None:
    a = email_id_hash_bucket("abc-123", seed="seed-a")
    b = email_id_hash_bucket("abc-123", seed="seed-b")
    assert a != b


def test_is_test_split_respects_ratio_boundaries() -> None:
    assert is_test_split("any-id", test_ratio=0.0) is False
    assert is_test_split("any-id", test_ratio=1.0) is True


def test_split_email_ids_no_overlap_and_stable() -> None:
    ids = [f"id-{index}" for index in range(200)]
    train_a, test_a = split_email_ids(ids, test_ratio=0.2, seed=DEFAULT_SPLIT_SEED)
    train_b, test_b = split_email_ids(ids, test_ratio=0.2, seed=DEFAULT_SPLIT_SEED)

    assert train_a == train_b
    assert test_a == test_b
    assert set(train_a).isdisjoint(test_a)
    assert len(train_a) + len(test_a) == len(ids)
    assert 20 <= len(test_a) <= 60


def test_split_email_ids_deduplicates() -> None:
    train, test = split_email_ids(["dup", "dup", "other"], test_ratio=0.5, seed="x")
    assert len(train) + len(test) == 2
