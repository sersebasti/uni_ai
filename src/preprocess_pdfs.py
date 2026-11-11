from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import fitz


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
        ["tesseract", "stdin", "stdout", "-l", languages],
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
        default="materials/pdf",
        type=Path,
        help="Directory containing lecture PDFs.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed",
        type=Path,
        help="Directory for the generated manifest and page renders.",
    )
    parser.add_argument(
        "--dpi",
        default=144,
        type=int,
        help="Render resolution for visual pages.",
    )
    parser.add_argument(
        "--ocr-dpi",
        default=300,
        type=int,
        help="Render resolution used by OCR.",
    )
    parser.add_argument(
        "--ocr-languages",
        default="ita+eng",
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

    pdf_paths = sorted(arguments.input_dir.glob("*.pdf"), key=lambda path: path.name.casefold())
    if arguments.max_files is not None:
        pdf_paths = pdf_paths[: arguments.max_files]
    if not pdf_paths:
        raise SystemExit(f"No PDF files found in: {arguments.input_dir}")

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

    temporary_manifest.replace(manifest_path)
    print(f"\nManifest: {manifest_path}")
    print(
        f"Pages: {page_count}; visual candidates: {visual_page_count}; "
        f"OCR pages: {ocr_page_count}"
    )


if __name__ == "__main__":
    main()