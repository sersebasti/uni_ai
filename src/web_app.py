from __future__ import annotations

import base64
import binascii
import json
import logging
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
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, OpenAIError
from pydantic import BaseModel, Field

from src.rag import chroma_client, create_embeddings
from src.sections import Section, section_config
from src.startup import Startup, StartupState

WEB_DIR = Path("web")
MEDIA_DIR = Path("data/generated")
DOCUMENTS_DIR = Path("materials/pdf")
MAX_SOURCES = 6
MAX_OVERVIEW_SOURCES = 20
MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    startups = {section: Startup(section) for section in Section}
    app.state.startups = startups
    app.state.startup = startups[Section.THEORY]
    for section, startup in startups.items():
        worker = threading.Thread(target=startup.run, name=f"startup-{section.value}", daemon=True)
        worker.start()
    try:
        yield
    finally:
        for startup in startups.values():
            startup.stop()


app = FastAPI(title="Uni AI", lifespan=lifespan)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2_000)
    section: Section = Section.THEORY
    cad_version: str = Field(default="", max_length=80)
    cad_language: str = Field(default="Italiano", min_length=1, max_length=80)


class Source(BaseModel):
    lecture: str
    page: int
    source_pdf: str
    document_url: str


class IllustrationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2_000)
    answer: str = Field(min_length=3, max_length=12_000)
    section: Section = Section.THEORY


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


def find_sources(
    question: str, section: Section = Section.THEORY,
) -> list[dict[str, object]]:
    try:
        collection = chroma_client().get_collection(section_config(section).collection_name)
        if section == Section.THEORY and is_course_overview(question):
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
        chat_request = ChatRequest.model_validate({
            "question": str(form.get("question", "")),
            "section": str(form.get("section", Section.THEORY.value)),
            "cad_version": str(form.get("cad_version", "")),
            "cad_language": str(form.get("cad_language", "Italiano")),
        })
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
def status(section: Section = Section.THEORY) -> StartupState:
    startup = app.state.startup if section == Section.THEORY else app.state.startups[section]
    return startup.snapshot()


def require_ready(section: Section = Section.THEORY) -> None:
    state = status(section)
    if not state["ready"]:
        if state["status"] == "empty":
            raise HTTPException(
                status_code=503,
                detail=f"Nessun PDF nella sezione {state['label']}. "
                f"Inserisci i documenti in {state['documents_dir']} e riavvia web.",
            )
        raise HTTPException(
            status_code=503,
            detail=state["error"] or "Preparazione in corso. Controlla lo stato di avanzamento.",
        )


@app.get("/api/documents/{filename}")
def document(filename: str, section: Section = Section.THEORY) -> FileResponse:
    if Path(filename).name != filename or not filename.endswith(".pdf"):
        raise HTTPException(status_code=404, detail="Dispensa non trovata.")

    directories = (
        [DOCUMENTS_DIR, DOCUMENTS_DIR / "teoria"]
        if section == Section.THEORY else [DOCUMENTS_DIR / "pratica"]
    )
    paths = [directory / filename for directory in directories if (directory / filename).is_file()]
    if not paths:
        raise HTTPException(status_code=404, detail="Dispensa non trovata.")
    if len(paths) > 1:
        raise HTTPException(status_code=409, detail="Nome PDF duplicato nella sezione. Rinomina i duplicati.")
    return FileResponse(paths[0], media_type="application/pdf")


@app.post("/api/chat")
async def chat(request: Request) -> dict[str, object]:
    chat_request, image_bytes, image_type = await parse_chat_request(request)
    require_ready(chat_request.section)
    sources = find_sources(chat_request.question, chat_request.section)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY non configurata.")

    system_prompt = (
        "Sei un assistente per lo studio universitario. Rispondi in italiano "
        "usando esclusivamente le fonti fornite. Se non bastano, dillo chiaramente. "
        "Non inventare informazioni o citazioni. Se l'utente allega un'immagine, "
        "usala come supporto alla domanda ma segnala eventuali dettagli che non puoi "
        "verificare nelle fonti."
    )
    question = chat_request.question
    if chat_request.section == Section.PRACTICE:
        system_prompt += (
            " Sei un tutor pratico di Solid Edge. Per le domande operative organizza la risposta "
            "in: ambiente e prerequisiti, comandi, procedura numerata, controlli del risultato. "
            "Usa solo procedure e nomi dei comandi supportati dai manuali forniti. "
            "Non inventare percorsi dei menu. Distingui la versione e la lingua richieste "
            "da quelle documentate; se la compatibilita non e verificabile, segnalalo "
            "e chiedi i dettagli mancanti. Non controlli direttamente il programma CAD."
        )
        question = (
            f"Versione Solid Edge richiesta: {chat_request.cad_version or 'non specificata'}\n"
            f"Lingua interfaccia CAD richiesta: {chat_request.cad_language}\n"
            f"Domanda: {question}"
        )

    try:
        completion = OpenAI(api_key=api_key).chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": build_user_content(
                        question,
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
                document_url=(
                    f"/api/documents/{quote(Path(str(source['source_pdf'])).name)}"
                    f"?section={chat_request.section.value}#page={int(source['page'])}"
                ),
            ).model_dump()
            for source in unique_sources.values()
        ],
    }


@app.post("/api/illustrations")
def generate_illustration(request: IllustrationRequest) -> dict[str, str]:
    require_ready(request.section)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY non configurata.")

    prompt = (
        "Create an original, clear educational diagram for a university student. "
        "Use concise Italian labels where needed. Do not reproduce source slides, logos, "
        "or long passages of text. Only depict concepts supported by this answer.\n\n"
        f"Student question: {request.question}\n\nAnswer to illustrate: {request.answer}"
    )
    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
    try:
        result = OpenAI(api_key=api_key).images.generate(
            model=model,
            prompt=prompt,
            size="1024x1024",
            quality="medium",
        )
    except APITimeoutError as error:
        logger.warning("Timeout durante la generazione immagine: model=%s", model)
        raise HTTPException(
            status_code=504, detail="OpenAI ha impiegato troppo tempo a generare l'immagine. Riprova."
        ) from error
    except APIConnectionError as error:
        logger.warning("Connessione OpenAI non disponibile per le immagini: model=%s", model)
        raise HTTPException(
            status_code=502, detail="Impossibile contattare OpenAI per generare l'immagine."
        ) from error
    except APIStatusError as error:
        logger.warning(
            "Generazione immagine rifiutata: model=%s status=%s code=%s request_id=%s",
            model, error.status_code, error.code, error.request_id,
        )
        if error.code in {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}:
            detail = "Credito o limite di spesa OpenAI esaurito. Controlla la fatturazione API."
        elif error.status_code == 401:
            detail = "La chiave API OpenAI non e valida. Controlla OPENAI_API_KEY."
        elif error.status_code == 403:
            detail = (
                "OpenAI non autorizza la generazione immagini. Controlla i permessi del "
                "progetto e l'eventuale verifica dell'organizzazione richiesta per il modello."
            )
        elif error.status_code == 404 or (error.status_code == 400 and error.param == "model"):
            detail = "Modello immagini non disponibile. Controlla OPENAI_IMAGE_MODEL e i permessi."
        elif error.status_code == 429:
            detail = "Limite di richieste OpenAI raggiunto. Attendi prima di riprovare."
        else:
            detail = (
                f"OpenAI ha rifiutato la generazione (HTTP {error.status_code}). "
                "Controlla modello, parametri e stato del servizio."
            )
        raise HTTPException(status_code=502, detail=detail) from error
    except OpenAIError as error:
        logger.warning("Errore OpenAI nella generazione immagine: %s", type(error).__name__)
        raise HTTPException(status_code=502, detail="OpenAI non ha generato l'immagine.") from error

    image_base64 = result.data[0].b64_json if result.data else None
    if not image_base64:
        logger.error("Risposta immagini OpenAI priva di dati: model=%s", model)
        raise HTTPException(status_code=502, detail="OpenAI non ha restituito un'immagine.")

    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except binascii.Error as error:
        logger.error("Dati immagine OpenAI non validi: model=%s", model)
        raise HTTPException(status_code=502, detail="OpenAI ha restituito un'immagine non valida.") from error

    filename = f"illustration-{uuid.uuid4().hex}.png"
    try:
        (MEDIA_DIR / filename).write_bytes(image_bytes)
    except OSError as error:
        logger.exception("Impossibile salvare l'immagine generata")
        raise HTTPException(
            status_code=500, detail="Impossibile salvare l'immagine. Controlla lo spazio e i permessi di data/generated."
        ) from error
    return {"image_url": f"/media/{filename}"}