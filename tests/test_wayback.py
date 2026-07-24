"""Tests for wayback."""

import wayback


def test_public_api() -> None:
    assert callable(wayback.get_url_snapshots)
    assert callable(wayback.get_url_list_snapshots)
