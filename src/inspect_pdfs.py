from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader


def inspect_pdf(path: Path) -> tuple[int, int, str | None]:
    try:
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return len(reader.pages), len(text.strip()), None
    except Exception as error:
        return 0, 0, str(error)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report page counts and extractable text for lecture PDFs."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="materials/pdf",
        type=Path,
        help="Directory containing PDF files (default: materials/pdf).",
    )
    arguments = parser.parse_args()

    if not arguments.directory.is_dir():
        raise SystemExit(f"PDF directory not found: {arguments.directory}")

    pdf_paths = sorted(arguments.directory.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"No PDF files found in: {arguments.directory}")

    print(f"Analysing {len(pdf_paths)} PDF files in {arguments.directory}\n")
    total_pages = 0
    total_characters = 0

    for path in pdf_paths:
        pages, characters, error = inspect_pdf(path)
        if error:
            print(f"{path.name}: ERROR - {error}")
            continue

        total_pages += pages
        total_characters += characters
        print(f"{path.name}: {pages} pages, {characters:,} extractable characters")

    print(f"\nTotal: {total_pages} pages, {total_characters:,} extractable characters")


if __name__ == "__main__":
    main()