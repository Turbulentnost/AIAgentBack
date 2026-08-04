"""Tests for purge_department_corrections_collection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_purge_delete_all() -> None:
    from agent_pochta.services.email_rag_qdrant import purge_department_corrections_collection

    point1 = MagicMock(id="p1", payload={"corrected_at": "2026-06-01T00:00:00"})
    point2 = MagicMock(id="p2", payload={"corrected_at": "2026-08-01T00:00:00"})

    client = MagicMock()
    client.scroll.side_effect = [([point1, point2], None)]

    with patch("agent_pochta.services.email_rag_qdrant.QdrantClient", return_value=client):
        stats = purge_department_corrections_collection(url="http://qdrant:6333", delete_all=True)

    assert stats["deleted"] == 2
    client.delete.assert_called_once()
