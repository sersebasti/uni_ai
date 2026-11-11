from __future__ import annotations

import os
from collections.abc import Sequence

import chromadb
import httpx

from src.sections import Section, section_config

COLLECTION_NAME = section_config(Section.THEORY).collection_name


def chroma_client() -> chromadb.ClientAPI:
    return chromadb.HttpClient(
        host=os.environ.get("CHROMA_HOST", "localhost"),
        port=int(os.environ.get("CHROMA_PORT", "8000")),
    )


def embedding_model() -> str:
    return os.environ.get("OLLAMA_EMBEDDING_MODEL", "bge-m3")


def embedding_model_digest() -> str:
    response = httpx.get(
        f"{os.environ.get('OLLAMA_URL', 'http://localhost:11434')}/api/tags",
        timeout=10,
    )
    response.raise_for_status()
    name = embedding_model()
    for model in response.json()["models"]:
        if model["name"] in {name, f"{name}:latest"} and model.get("digest"):
            return model["digest"]
    raise RuntimeError(f"Modello embedding non disponibile in Ollama: {name}")


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