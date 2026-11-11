from __future__ import annotations

import os
from collections.abc import Sequence

import chromadb
import httpx

COLLECTION_NAME = "university_notes"


def chroma_client() -> chromadb.ClientAPI:
    return chromadb.HttpClient(
        host=os.environ.get("CHROMA_HOST", "localhost"),
        port=int(os.environ.get("CHROMA_PORT", "8000")),
    )


def embedding_model() -> str:
    return os.environ.get("OLLAMA_EMBEDDING_MODEL", "bge-m3")


def create_embeddings(texts: Sequence[str]) -> list[list[float]]:
    response = httpx.post(
        f"{os.environ.get('OLLAMA_URL', 'http://localhost:11434')}/api/embed",
        json={"model": embedding_model(), "input": list(texts)},
        timeout=120,
    )
    response.raise_for_status()
    embeddings = response.json().get("embeddings")
    if not embeddings:
        raise RuntimeError("Ollama did not return embeddings.")
    return embeddings