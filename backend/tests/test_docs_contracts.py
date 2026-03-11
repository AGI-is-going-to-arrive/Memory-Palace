import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _extract_section_numbers(path: Path, *, chapter: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^### ({re.escape(chapter)}\.\d+)\b", re.MULTILINE)
    return pattern.findall(text)


def test_getting_started_section_four_numbers_are_unique_and_ordered() -> None:
    assert _extract_section_numbers(
        PROJECT_ROOT / "docs" / "GETTING_STARTED.md",
        chapter="4",
    ) == ["4.1", "4.2", "4.3", "4.4"]
    assert _extract_section_numbers(
        PROJECT_ROOT / "docs" / "GETTING_STARTED_EN.md",
        chapter="4",
    ) == ["4.1", "4.2", "4.3", "4.4"]
