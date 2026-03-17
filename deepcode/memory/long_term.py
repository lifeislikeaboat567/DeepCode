"""ChromaDB-backed long-term vector memory for DeepCode Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepcode.config import get_settings
from deepcode.logging_config import get_logger

logger = get_logger(__name__)


class LongTermMemory:
    """Persistent vector memory powered by ChromaDB.

    Stores text entries as embeddings so that semantically similar content
    can be retrieved efficiently.  The ChromaDB collection is persisted to
    disk in ``settings.data_dir``.

    Args:
        collection_name: ChromaDB collection name (default from settings).
        persist_dir: Directory for ChromaDB persistence (default from settings).
    """

    def __init__(
        self,
        collection_name: str | None = None,
        persist_dir: Path | None = None,
    ) -> None:
        settings = get_settings()
        self._collection_name = collection_name or settings.vector_collection
        self._persist_dir = str(persist_dir or settings.data_dir / "chroma")
        self._collection: Any = None  # lazy-initialised

    def _get_collection(self) -> Any:
        """Lazily initialise and return the ChromaDB collection."""
        if self._collection is not None:
            return self._collection

        try:
            import chromadb  # type: ignore[import]
            from chromadb.config import Settings as ChromaSettings  # type: ignore[import]

            client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except ImportError:
            logger.warning(
                "chromadb not installed – long-term memory is disabled. "
                "Install with: pip install chromadb"
            )
            self._collection = _NullCollection()

        return self._collection

    def add(self, entry_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        """Store *text* with the given *entry_id*.

        Args:
            entry_id: Unique identifier for the entry.
            text: Text content to embed and store.
            metadata: Optional key-value metadata attached to the entry.
        """
        collection = self._get_collection()
        try:
            collection.add(
                documents=[text],
                ids=[entry_id],
                metadatas=[metadata or {}],
            )
        except Exception as exc:
            logger.error("Failed to add memory entry", entry_id=entry_id, error=str(exc))

    def query(
        self,
        query_text: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Find the *n_results* most relevant entries for *query_text*.

        Args:
            query_text: The search query.
            n_results: Number of results to return.

        Returns:
            List of dicts with ``id``, ``document``, ``metadata``, and ``distance``.
        """
        collection = self._get_collection()
        try:
            results = collection.query(
                query_texts=[query_text],
                n_results=n_results,
            )
        except Exception as exc:
            logger.error("Long-term memory query failed", error=str(exc))
            return []

        hits: list[dict[str, Any]] = []
        ids = (results.get("ids") or [[]])[0]
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        for i, entry_id in enumerate(ids):
            hits.append(
                {
                    "id": entry_id,
                    "document": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                    "distance": distances[i] if i < len(distances) else 1.0,
                }
            )
        return hits

    def delete(self, entry_id: str) -> None:
        """Remove an entry by its ID.

        Args:
            entry_id: The ID of the entry to remove.
        """
        collection = self._get_collection()
        try:
            collection.delete(ids=[entry_id])
        except Exception as exc:
            logger.error("Failed to delete memory entry", entry_id=entry_id, error=str(exc))


class _NullCollection:
    """No-op collection used when ChromaDB is not installed."""

    def add(self, **_: Any) -> None:
        pass

    def query(self, **_: Any) -> dict[str, Any]:
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def delete(self, **_: Any) -> None:
        pass
