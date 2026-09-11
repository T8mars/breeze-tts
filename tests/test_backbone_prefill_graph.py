from __future__ import annotations

import threading

from models.cudagraph.backbone_prefill_graph import BackbonePrefillGraphCache


def _cache_with_keys(*keys: tuple[int, int]) -> BackbonePrefillGraphCache:
    cache = object.__new__(BackbonePrefillGraphCache)
    cache.token_granularity = 32
    cache._records = {key: object() for key in keys}
    cache._lock = threading.RLock()
    cache._frozen = True
    return cache


def test_graph_key_rounds_257_tokens_to_reported_288_bucket() -> None:
    cache = _cache_with_keys((1, 256), (1, 288), (1, 512))

    assert cache.graph_key(branch_batch_size=1, sequence_length=256) == (1, 256)
    assert cache.graph_key(branch_batch_size=1, sequence_length=257) == (1, 288)
    assert cache.graph_key(branch_batch_size=1, sequence_length=288) == (1, 288)
    assert cache.graph_key(branch_batch_size=1, sequence_length=512) == (1, 512)
    assert cache.graph_key(branch_batch_size=1, sequence_length=513) == (1, 544)


def test_frozen_cache_reports_declared_and_missing_shapes() -> None:
    cache = _cache_with_keys((1, 256), (1, 288), (1, 512), (2, 512))

    assert cache.frozen is True
    assert cache.has_graph_key((1, 288)) is True
    assert cache.has_graph_key((1, 544)) is False
    assert cache.has_graph_key((2, 512)) is True
