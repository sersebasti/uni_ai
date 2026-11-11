from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Section(str, Enum):
    THEORY = "theory"
    PRACTICE = "practice"


@dataclass(frozen=True)
class SectionConfig:
    section: Section
    label: str
    input_dir: Path
    output_dir: Path
    collection_name: str

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / "pages.jsonl"

    @property
    def documents_dir(self) -> Path:
        return self.input_dir / "teoria" if self.section == Section.THEORY else self.input_dir


SECTIONS = {
    Section.THEORY: SectionConfig(
        Section.THEORY, "Teoria", Path("materials/pdf"), Path("data/processed"), "university_notes"
    ),
    Section.PRACTICE: SectionConfig(
        Section.PRACTICE, "Pratica - Solid Edge", Path("materials/pdf/pratica"),
        Path("data/processed/pratica"), "solid_edge_manuals",
    ),
}


def section_config(section: Section) -> SectionConfig:
    return SECTIONS[section]
