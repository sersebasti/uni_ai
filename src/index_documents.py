from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from chromadb.errors import NotFoundError

from src.preprocess_pdfs import file_digest
from src.rag import (
    COLLECTION_NAME,
    chroma_client,
    create_embeddings,
    embedding_model,
    embedding_model_digest,
)

MANIFEST_PATH = Path("data/processed/pages.jsonl")
CHUNK_SIZE = 1_200
CHUNK_OVERLAP = 200
EMBEDDING_BATCH_SIZE = 32
INDEX_VERSION = 1
logger = logging.getLogger(__name__)


def index_fingerprint() -> str:
    if not MANIFEST_PATH.is_file():
        raise SystemExit("Manifest not found. Run the preprocess service first.")
    settings = {
        "version": INDEX_VERSION,
        "manifest": file_digest(MANIFEST_PATH),
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "embedding_model": embedding_model(),
        "embedding_digest": embedding_model_digest(),
    }
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def cached_chunk_count() -> int | None:
    try:
        collection = chroma_client().get_collection(COLLECTION_NAME)
    except NotFoundError:
        logger.info("Indice da creare: collezione non trovata")
        return None
    metadata = collection.metadata or {}
    count = metadata.get("indexed_chunks")
    if (
        type(count) is not int
        or count <= 0
        or metadata.get("index_fingerprint") != index_fingerprint()
        or collection.count() != count
    ):
        logger.info("Indice da ricreare: firma, modello o numero di blocchi non corrispondenti")
        return None
    return count


def chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    start = 0
    text = " ".join(text.split())

    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            split_at = text.rfind(" ", start, end)
            if split_at > start:
                end = split_at
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)

    return chunks


def load_chunks() -> list[tuple[str, dict[str, int | str]]]:
    if not MANIFEST_PATH.is_file():
        raise SystemExit("Manifest not found. Run the preprocess service first.")

    chunks: list[tuple[str, dict[str, int | str]]] = []
    with MANIFEST_PATH.open(encoding="utf-8") as manifest:
        for line in manifest:
            page = json.loads(line)
            native_text = str(page.get("text", ""))
            ocr_text = str(page.get("ocr_text", ""))
            searchable_text = native_text
            if ocr_text and ocr_text not in native_text:
                searchable_text = f"{native_text}\n\nTesto estratto dalle immagini:\n{ocr_text}"

            for chunk_number, text in enumerate(chunk_text(searchable_text), start=1):
                chunks.append(
                    (
                        text,
                        {
                            "lecture": page["lecture"],
                            "page": page["page"],
                            "source_pdf": page["source_pdf"],
                            "chunk": chunk_number,
                        },
                    )
                )
    return chunks


def make_id(text: str, metadata: dict[str, int | str]) -> str:
    source = f"{metadata['source_pdf']}:{metadata['page']}:{metadata['chunk']}:{text}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def main(progress: Callable[[int, int, str], None] | None = None) -> None:
    fingerprint = index_fingerprint()
    chunks = load_chunks()
    if not chunks:
        raise SystemExit("No text was found in the manifest.")
    if progress:
        progress(0, len(chunks), "Creazione embedding e indice vettoriale")

    client = chroma_client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except NotFoundError:
        pass
    collection = client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    for batch_start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[batch_start : batch_start + EMBEDDING_BATCH_SIZE]
        documents = [text for text, _ in batch]
        metadatas = [metadata for _, metadata in batch]
        collection.upsert(
            ids=[make_id(text, metadata) for text, metadata in batch],
            documents=documents,
            metadatas=metadatas,
            embeddings=create_embeddings(documents),
        )
        print(f"Indexed {min(batch_start + len(batch), len(chunks))}/{len(chunks)} chunks")
        if progress:
            progress(batch_start + len(batch), len(chunks), "Blocchi indicizzati")

    if index_fingerprint() != fingerprint:
        raise RuntimeError("Manifest o modello cambiati durante l'indicizzazione. Ripeti l'operazione.")
    collection.modify(
        metadata={
            "index_fingerprint": fingerprint,
            "indexed_chunks": len(chunks),
        }
    )
    print(f"Vector index ready: {len(chunks)} chunks")


if __name__ == "__main__":
    main()