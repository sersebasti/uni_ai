from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import TypedDict

import httpx

from src import index_documents, preprocess_pdfs
from src.rag import chroma_client, embedding_model

logger = logging.getLogger(__name__)
STAGES = (
    ("services", "Avvio servizi locali"),
    ("model", "Preparazione modello embedding"),
    ("preprocess", "Preprocessing PDF e OCR"),
    ("index", "Indicizzazione"),
)


class StageState(TypedDict):
    id: str
    label: str
    status: str
    completed: int
    total: int | None
    detail: str


class StartupState(TypedDict):
    status: str
    ready: bool
    error: str | None
    stages: list[StageState]


class Startup:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: StartupState = {
            "status": "preparing",
            "ready": False,
            "error": None,
            "stages": [
                {
                    "id": stage_id,
                    "label": label,
                    "status": "pending",
                    "completed": 0,
                    "total": None,
                    "detail": "In attesa",
                }
                for stage_id, label in STAGES
            ],
        }

    def snapshot(self) -> StartupState:
        with self._lock:
            return copy.deepcopy(self._state)

    def stop(self) -> None:
        self._stop.set()

    def update(
        self,
        stage_id: str,
        completed: int = 0,
        total: int | None = None,
        detail: str = "",
        status: str = "running",
    ) -> None:
        if self._stop.is_set():
            raise InterruptedError("Preparazione interrotta")
        with self._lock:
            for stage in self._state["stages"]:
                if stage["id"] == stage_id:
                    stage.update(
                        status=status, completed=completed, total=total, detail=detail
                    )
                    break

    def progress(self, stage_id: str) -> Callable[[int, int, str], None]:
        return lambda completed, total, detail: self.update(
            stage_id, completed, total, detail
        )

    def wait_for_services(self) -> None:
        deadline = time.monotonic() + 180
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        while True:
            self.update("services", detail="Connessione a Ollama e ChromaDB")
            try:
                response = httpx.get(f"{url}/api/tags", timeout=5)
                response.raise_for_status()
                chroma_client().heartbeat()
                return
            except Exception as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Servizi locali non disponibili dopo 180 secondi: {error}"
                    ) from error
                logger.warning("Servizi non ancora pronti: %s", error)
                if self._stop.wait(2):
                    raise InterruptedError("Preparazione interrotta") from error
                self.update("services", detail=f"In attesa dei servizi: {error}")

    def prepare_model(self) -> None:
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        with httpx.stream(
            "POST",
            f"{url}/api/pull",
            json={"name": embedding_model(), "stream": True},
            timeout=600,
        ) as response:
            response.raise_for_status()
            succeeded = False
            for line in response.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(event["error"])
                self.update(
                    "model",
                    completed=event.get("completed", 0),
                    total=event.get("total"),
                    detail=f"{embedding_model()}: {event.get('status', 'Download')}",
                )
                succeeded = event.get("status") == "success"
            if not succeeded:
                raise RuntimeError("Download del modello interrotto prima del completamento.")

    def run(self) -> None:
        current_stage = "services"
        try:
            for stage_id, _ in STAGES:
                current_stage = stage_id
                self.update(stage_id, detail="Avvio attivita")
                if stage_id == "services":
                    self.wait_for_services()
                elif stage_id == "model":
                    self.prepare_model()
                elif stage_id == "preprocess":
                    preprocess_pdfs.main([], progress=self.progress(stage_id))
                else:
                    index_documents.main(progress=self.progress(stage_id))
                with self._lock:
                    stage = next(
                        stage for stage in self._state["stages"] if stage["id"] == stage_id
                    )
                    stage.update(status="done", detail="Completato")
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError(
                    "OPENAI_API_KEY non configurata. Impostala nel file .env e riavvia web."
                )
            with self._lock:
                self._state.update(status="ready", ready=True)
        except InterruptedError:
            logger.info("Preparazione interrotta durante lo spegnimento")
        except (Exception, SystemExit) as error:
            logger.exception("Preparazione fallita: %s", error)
            with self._lock:
                self._state.update(status="error", ready=False, error=str(error))
                for stage in self._state["stages"]:
                    if stage["id"] == current_stage and stage["status"] != "done":
                        stage.update(status="error", detail=str(error))
