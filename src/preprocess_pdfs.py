from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)
PREPROCESS_VERSION = 1
INPUT_DIR = Path("materials/pdf")
OUTPUT_DIR = Path("data/processed")
DEFAULT_DPI = 144
DEFAULT_OCR_DPI = 300
DEFAULT_OCR_LANGUAGES = "ita+eng"


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def find_pdf_paths(input_dir: Path, max_files: int | None = None) -> list[Path]:
    paths = sorted(input_dir.glob("*.pdf"), key=lambda path: path.name.casefold())
    return paths if max_files is None else paths[:max_files]


def preprocessing_fingerprint(
    pdf_paths: list[Path],
    dpi: int = DEFAULT_DPI,
    ocr_dpi: int = DEFAULT_OCR_DPI,
    ocr_languages: str = DEFAULT_OCR_LANGUAGES,
    skip_ocr: bool = False,
) -> str:
    settings = {
        "version": PREPROCESS_VERSION,
        "pymupdf": fitz.VersionBind,
        "dpi": dpi,
        "ocr_dpi": ocr_dpi,
        "ocr_languages": ocr_languages,
        "skip_ocr": skip_ocr,
        "pdfs": [(path.as_posix(), file_digest(path)) for path in pdf_paths],
    }
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def cached_page_count() -> int | None:
    manifest_path = OUTPUT_DIR / "pages.jsonl"
    cache_path = OUTPUT_DIR / "preprocess-state.json"
    if not manifest_path.is_file() or not cache_path.is_file():
        logger.info("Preprocessing da eseguire: risultati o firma persistente mancanti")
        return None
    try:
        state = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Firma preprocessing non valida: i risultati saranno rigenerati")
        return None
    if not isinstance(state, dict) or type(state.get("pages")) is not int or state["pages"] <= 0:
        logger.warning("Stato preprocessing incompleto: i risultati saranno rigenerati")
        return None
    pdf_paths = find_pdf_paths(INPUT_DIR)
    if (
        not pdf_paths
        or state.get("input_fingerprint") != preprocessing_fingerprint(pdf_paths)
        or state.get("manifest_digest") != file_digest(manifest_path)
    ):
        logger.info("Preprocessing da eseguire: PDF, impostazioni o manifest modificati")
        return None
    with manifest_path.open(encoding="utf-8") as manifest:
        for line in manifest:
            render_path = json.loads(line).get("render_path")
            if render_path and not Path(render_path).is_file():
                logger.info("Preprocessing da eseguire: rendering mancante (%s)", render_path)
                return None
    return state["pages"]


def page_is_visual(drawing_count: int, text_length: int) -> bool:
    return drawing_count >= 10 or text_length < 120


def render_page(page: fitz.Page, destination: Path, dpi: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pixmap.save(destination)


def ocr_page(page: fitz.Page, dpi: int, languages: str) -> str:
    scale = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", languages, "--psm", "1"],
        input=pixmap.tobytes("png"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.decode("utf-8").strip()


def process_pdf(
    pdf_path: Path,
    output_dir: Path,
    dpi: int,
    ocr_dpi: int,
    ocr_languages: str,
    skip_ocr: bool,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, object]]:
    document = fitz.open(pdf_path)
    records: list[dict[str, object]] = []
    lecture_render_dir = output_dir / "renders" / pdf_path.stem
    shutil.rmtree(lecture_render_dir, ignore_errors=True)

    try:
        for page_index, page in enumerate(document):
            text = page.get_text("text").strip()
            image_count = len(page.get_images(full=True))
            drawing_count = len(page.get_drawings())
            visual = page_is_visual(drawing_count, len(text))
            needs_ocr = visual or image_count > 0
            render_path: Path | None = None
            ocr_text = ""

            if visual:
                render_path = lecture_render_dir / f"page-{page_index + 1:03}.png"
                render_page(page, render_path, dpi)
            if needs_ocr and not skip_ocr:
                ocr_text = ocr_page(page, ocr_dpi, ocr_languages)

            records.append(
                {
                    "lecture": pdf_path.stem,
                    "source_pdf": pdf_path.as_posix(),
                    "page": page_index + 1,
                    "text": text,
                    "text_characters": len(text),
                    "image_count": image_count,
                    "drawing_count": drawing_count,
                    "needs_visual_analysis": visual,
                    "render_path": render_path.as_posix() if render_path else None,
                    "ocr_applied": needs_ocr and not skip_ocr,
                    "ocr_text": ocr_text,
                    "ocr_characters": len(ocr_text),
                }
            )
            if progress:
                progress(f"{pdf_path.name} - pagina {page_index + 1}/{len(document)}")
    finally:
        document.close()

    return records


def main(
    argv: Sequence[str] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> None:
    parser = argparse.ArgumentParser(
        description="Extract lecture text and render pages that need visual analysis."
    )
    parser.add_argument(
        "--input-dir",
        default=INPUT_DIR,
        type=Path,
        help="Directory containing lecture PDFs.",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        type=Path,
        help="Directory for the generated manifest and page renders.",
    )
    parser.add_argument(
        "--dpi",
        default=DEFAULT_DPI,
        type=int,
        help="Render resolution for visual pages.",
    )
    parser.add_argument(
        "--ocr-dpi",
        default=DEFAULT_OCR_DPI,
        type=int,
        help="Render resolution used by OCR.",
    )
    parser.add_argument(
        "--ocr-languages",
        default=DEFAULT_OCR_LANGUAGES,
        help="Tesseract language codes used for OCR.",
    )
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="Skip OCR, useful for a faster preprocessing run.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        help="Process only the first N PDFs, useful for a fast test.",
    )
    arguments = parser.parse_args(argv)

    if not arguments.input_dir.is_dir():
        raise SystemExit(f"PDF directory not found: {arguments.input_dir}")
    if arguments.dpi <= 0 or arguments.ocr_dpi <= 0:
        raise SystemExit("DPI values must be greater than zero.")

    pdf_paths = find_pdf_paths(arguments.input_dir, arguments.max_files)
    if not pdf_paths:
        raise SystemExit(f"No PDF files found in: {arguments.input_dir}")

    def fingerprint(paths: list[Path]) -> str:
        return preprocessing_fingerprint(
            paths,
            arguments.dpi,
            arguments.ocr_dpi,
            arguments.ocr_languages,
            arguments.skip_ocr,
        )

    input_fingerprint = fingerprint(pdf_paths)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = arguments.output_dir / "pages.jsonl"
    page_count = 0
    visual_page_count = 0
    ocr_page_count = 0
    total_pages = 0
    for pdf_path in pdf_paths:
        with fitz.open(pdf_path) as document:
            total_pages += len(document)

    completed_pages = 0

    def page_processed(detail: str) -> None:
        nonlocal completed_pages
        completed_pages += 1
        if progress:
            progress(completed_pages, total_pages, detail)

    if progress:
        progress(0, total_pages, "Estrazione testo, rendering e OCR")

    temporary_manifest = manifest_path.with_suffix(".jsonl.tmp")
    with temporary_manifest.open("w", encoding="utf-8") as manifest:
        for pdf_path in pdf_paths:
            records = process_pdf(
                pdf_path,
                arguments.output_dir,
                arguments.dpi,
                arguments.ocr_dpi,
                arguments.ocr_languages,
                arguments.skip_ocr,
                page_processed,
            )
            for record in records:
                manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
                page_count += 1
                visual_page_count += int(record["needs_visual_analysis"])
                ocr_page_count += int(record["ocr_applied"])
            print(f"{pdf_path.name}: {len(records)} pages processed")

    if fingerprint(find_pdf_paths(arguments.input_dir, arguments.max_files)) != input_fingerprint:
        raise RuntimeError("I PDF sono cambiati durante il preprocessing. Ripeti l'elaborazione.")
    temporary_manifest.replace(manifest_path)
    cache_path = arguments.output_dir / "preprocess-state.json"
    temporary_cache = cache_path.with_suffix(".json.tmp")
    temporary_cache.write_text(
        json.dumps(
            {
                "input_fingerprint": input_fingerprint,
                "manifest_digest": file_digest(manifest_path),
                "pages": page_count,
            }
        ),
        encoding="utf-8",
    )
    temporary_cache.replace(cache_path)
    print(f"\nManifest: {manifest_path}")
    print(
        f"Pages: {page_count}; visual candidates: {visual_page_count}; "
        f"OCR pages: {ocr_page_count}"
    )


if __name__ == "__main__":
    main()