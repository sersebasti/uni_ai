from __future__ import annotations

import base64
import json
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from chromadb.errors import NotFoundError
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field

from src.rag import COLLECTION_NAME, chroma_client, create_embeddings
from src.startup import Startup, StartupState

WEB_DIR = Path("web")
MEDIA_DIR = Path("data/generated")
DOCUMENTS_DIR = Path("materials/pdf")
MAX_SOURCES = 6
MAX_OVERVIEW_SOURCES = 20
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    startup = Startup()
    app.state.startup = startup
    worker = threading.Thread(target=startup.run, name="startup", daemon=True)
    worker.start()
    try:
        yield
    finally:
        startup.stop()


app = FastAPI(title="Uni AI", lifespan=lifespan)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2_000)


class Source(BaseModel):
    lecture: str
    page: int
    source_pdf: str
    document_url: str


class IllustrationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2_000)
    answer: str = Field(min_length=3, max_length=12_000)


def is_course_overview(question: str) -> bool:
    normalized = " ".join(question.casefold().split())
    return bool(
        re.search(r"\b(corso|dispense|lezioni)\b", normalized)
        and re.search(
            r"\b(di cosa parla|di cosa tratta|panoramica|programma|"
            r"argomenti|riassunto|sintesi|presentazione|descrizione)\b",
            normalized,
        )
    )


def find_sources(question: str) -> list[dict[str, object]]:
    try:
        collection = chroma_client().get_collection(COLLECTION_NAME)
        if is_course_overview(question):
            overview = collection.get(
                where={"page": 1},
                include=["documents", "metadatas"],
            )
            candidates = sorted(
                [
                    {"text": document, **metadata}
                    for document, metadata in zip(
                        overview["documents"], overview["metadatas"], strict=True
                    )
                ],
                key=lambda source: (str(source["source_pdf"]), int(source["chunk"])),
            )
            sources = []
            seen_documents = set()
            for source in candidates:
                document = str(source["source_pdf"])
                if document not in seen_documents:
                    sources.append(source)
                    seen_documents.add(document)
            sources = sources[:MAX_OVERVIEW_SOURCES]
        else:
            results = collection.query(
                query_embeddings=create_embeddings([question]),
                n_results=MAX_SOURCES,
                include=["documents", "metadatas"],
            )
            sources = [
                {"text": document, **metadata}
                for document, metadata in zip(
                    results["documents"][0], results["metadatas"][0], strict=True
                )
            ]
    except NotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail="Indice non trovato. Avvia prima il servizio indexer.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Il servizio di ricerca locale non e disponibile.",
        ) from error

    if not sources:
        raise HTTPException(
            status_code=503,
            detail="L'indice non contiene fonti utilizzabili per questa domanda.",
        )
    return sources


def build_context(sources: list[dict[str, object]]) -> str:
    return "\n\n".join(
        f"[Lezione: {source['lecture']}; pagina: {source['page']}]\n{source['text']}"
        for source in sources
    )


async def parse_chat_request(request: Request) -> tuple[ChatRequest, bytes | None, str | None]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            return ChatRequest.model_validate(await request.json()), None, None
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Domanda non valida.") from error

    form = await request.form()
    try:
        chat_request = ChatRequest(question=str(form.get("question", "")))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Domanda non valida.") from error

    attachment = form.get("image")
    if not isinstance(attachment, UploadFile) or not attachment.filename:
        return chat_request, None, None
    if attachment.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Usa un'immagine PNG, JPEG o WebP.")

    image_bytes = await attachment.read(MAX_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="L'immagine supera il limite di 10 MB.")
    return chat_request, image_bytes, attachment.content_type


def build_user_content(
    question: str,
    sources: list[dict[str, object]],
    image_bytes: bytes | None,
    image_type: str | None,
) -> str | list[dict[str, object]]:
    content = f"Domanda: {question}\n\nFonti:\n{build_context(sources)}"
    if not image_bytes or not image_type:
        return content

    image_data = base64.b64encode(image_bytes).decode("ascii")
    return [
        {"type": "text", "text": content},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image_type};base64,{image_data}"},
        },
    ]


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/status")
def status() -> StartupState:
    return app.state.startup.snapshot()


def require_ready() -> None:
    state = app.state.startup.snapshot()
    if not state["ready"]:
        raise HTTPException(
            status_code=503,
            detail=state["error"] or "Preparazione in corso. Controlla lo stato di avanzamento.",
        )


@app.get("/api/documents/{filename}")
def document(filename: str) -> FileResponse:
    if Path(filename).name != filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=404, detail="Dispensa non trovata.")

    pdf_path = DOCUMENTS_DIR / filename
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="Dispensa non trovata.")
    return FileResponse(pdf_path, media_type="application/pdf")


@app.post("/api/chat")
async def chat(request: Request) -> dict[str, object]:
    require_ready()
    chat_request, image_bytes, image_type = await parse_chat_request(request)
    sources = find_sources(chat_request.question)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY non configurata.")

    try:
        completion = OpenAI(api_key=api_key).chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei un assistente per lo studio universitario. Rispondi in italiano "
                        "usando esclusivamente le fonti fornite. Se non bastano, dillo chiaramente. "
                        "Non inventare informazioni o citazioni. Se l'utente allega un'immagine, "
                        "usala come supporto alla domanda ma segnala eventuali dettagli che non puoi "
                        "verificare nelle fonti."
                    ),
                },
                {
                    "role": "user",
                    "content": build_user_content(
                        chat_request.question,
                        sources,
                        image_bytes,
                        image_type,
                    ),
                },
            ],
        )
    except OpenAIError as error:
        raise HTTPException(status_code=502, detail="OpenAI non ha completato la risposta.") from error
    answer = completion.choices[0].message.content or "Non sono riuscito a generare una risposta."
    unique_sources = {
        (str(source["source_pdf"]), int(source["page"])): source for source in sources
    }

    return {
        "answer": answer,
        "sources": [
            Source(
                lecture=str(source["lecture"]),
                page=int(source["page"]),
                source_pdf=str(source["source_pdf"]),
                document_url=f"/api/documents/{quote(Path(str(source['source_pdf'])).name)}",
            ).model_dump()
            for source in unique_sources.values()
        ],
    }


@app.post("/api/illustrations")
def generate_illustration(request: IllustrationRequest) -> dict[str, str]:
    require_ready()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY non configurata.")

    prompt = (
        "Create an original, clear educational diagram for a university student. "
        "Use concise Italian labels where needed. Do not reproduce source slides, logos, "
        "or long passages of text. Only depict concepts supported by this answer.\n\n"
        f"Student question: {request.question}\n\nAnswer to illustrate: {request.answer}"
    )
    try:
        result = OpenAI(api_key=api_key).images.generate(
            model=os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            prompt=prompt,
            size="1024x1024",
            quality="medium",
        )
    except OpenAIError as error:
        raise HTTPException(status_code=502, detail="OpenAI non ha generato l'immagine.") from error

    image_base64 = result.data[0].b64_json
    if not image_base64:
        raise HTTPException(status_code=502, detail="OpenAI non ha restituito un'immagine.")

    filename = f"illustration-{uuid.uuid4().hex}.png"
    (MEDIA_DIR / filename).write_bytes(base64.b64decode(image_base64))
    return {"image_url": f"/media/{filename}"}