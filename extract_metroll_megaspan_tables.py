import json
import re
from pathlib import Path

from pypdf import PdfReader


PDF_PATH = Path(r"C:\Users\SamLawless\Downloads\metroll_MEGASPAN_Purlin_Design_Manual_25.pdf")
OUT_PATH = Path(__file__).resolve().parent / "metroll_megaspan_capacity_tables.json"
SPANS_MM = list(range(3000, 12001, 500))

PAGE_TABLES = {
    14: ("1A", "single", "outward"),
    15: ("1B", "single", "inward"),
    16: ("2A", "double", "outward"),
    17: ("2B", "double", "inward"),
    22: ("5A", "continuous_lapped", "outward"),
    23: ("5B", "continuous_lapped", "inward"),
}


def parse_page(text, direction):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    profiles = []
    current = None

    def finish_current():
        if current is not None and current["rows"]:
            profiles.append(current)

    for line in lines:
        header = re.fullmatch(r"C/Z\s+(\d+)\s+(\d+)", line)
        if header:
            finish_current()
            depth, thickness = header.groups()
            current = {
                "section": f"C/Z {depth} {thickness}",
                "depth_mm": int(depth),
                "thickness_tenths_mm": int(thickness),
                "rows": [],
            }
            continue
        if current is None:
            continue
        if line.startswith("B0") or line.startswith("Span"):
            continue
        numbers = [float(value) for value in re.findall(r"\d+\.\d+", line)]
        if len(numbers) < 2 or len(current["rows"]) >= len(SPANS_MM):
            continue
        row = {"span_mm": SPANS_MM[len(current["rows"])]}
        if direction == "inward":
            row["strength_capacity_kn_m"] = max(numbers[:-1] or numbers)
            row["service_l150_capacity_kn_m"] = numbers[-1]
        else:
            row["strength_capacity_kn_m"] = max(numbers)
        current["rows"].append(row)

    finish_current()
    return profiles


def main():
    reader = PdfReader(str(PDF_PATH))
    tables = {}
    for page_number, (table_id, span_type, direction) in PAGE_TABLES.items():
        text = reader.pages[page_number - 1].extract_text() or ""
        profiles = parse_page(text, direction)
        tables[table_id] = {
            "page": page_number,
            "span_type": span_type,
            "direction": direction,
            "profiles": profiles,
        }
    OUT_PATH.write_text(json.dumps({"source": PDF_PATH.name, "tables": tables}, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
