"""
Build a TMDL script with every DAX measure, from the single source of truth
powerbi/measures.dax.

Usage:
    python -m src.powerbi_tmdl          # writes powerbi/measures.tmdl

Paste the result in Power BI Desktop > TMDL view > Apply. It creates (or
replaces) the table `_Measures` with all measures, their display folders,
format strings and descriptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src import config as cfg

DAX_FILE = cfg.ROOT_DIR / "powerbi" / "measures.dax"
TMDL_FILE = cfg.ROOT_DIR / "powerbi" / "measures.tmdl"

FOLDER_RE = re.compile(r"^// -+ \[(.+)\]\s*$")
MEASURE_RE = re.compile(r"^(?!VAR\b|RETURN\b)([A-Z0-9][^/=]*?) =\s*$")

MONEY = {"Revenue", "AOV", "Freight", "Revenue PM", "Revenue PY",
         "Revenue per Customer", "AOV by Order"}
REVIEW = {"Avg Review Score", "Avg Review On Time", "Avg Review Late",
          "Review Gap Late vs On Time", "Seller Avg Review"}


@dataclass
class Measure:
    name: str
    folder: str
    lines: list[str]

    @property
    def description(self) -> str:
        first = next((ln.strip() for ln in self.lines if ln.strip()), "")
        return first[2:].strip() if first.startswith("//") else ""

    @property
    def format_string(self) -> str | None:
        n = self.name
        if "%" in n:
            return "0.0%"
        if n in MONEY:
            return "R$ #,0.00"
        if n in REVIEW:
            return "0.00"
        if "Days" in n or "Installments" in n or "Delay" in n:
            return "0.0"
        if n == "Seller Risk Flag":
            return "0"
        if n == "Last Refresh Label":
            return None
        return "#,0"            # counts


def parse_dax(text: str) -> list[Measure]:
    measures: list[Measure] = []
    folder = ""
    current: Measure | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if m := FOLDER_RE.match(line):
            folder = m.group(1)
            current = None
            continue
        if m := MEASURE_RE.match(line):
            current = Measure(m.group(1).strip(), folder, [])
            measures.append(current)
            continue
        if current is not None:
            current.lines.append(line)
    for ms in measures:                      # trim trailing blank lines
        while ms.lines and not ms.lines[-1].strip():
            ms.lines.pop()
    return measures


def to_tmdl(measures: list[Measure]) -> str:
    # Note: TMDL has no // comments outside expressions, so the script starts directly
    out = [
        "createOrReplace",
        "",
        "\ttable _Measures",
        "",
    ]
    for ms in measures:
        if ms.description:
            out.append(f"\t\t/// {ms.description}")
        out.append(f"\t\tmeasure '{ms.name}' =")
        out += [f"\t\t\t\t{ln}" if ln.strip() else "" for ln in ms.lines]
        if ms.format_string:
            out.append(f"\t\t\tformatString: {ms.format_string}")
        out.append(f"\t\t\tdisplayFolder: {ms.folder}")
        out.append("")
    out += [
        # Power Query (M) partition: same type as a table created with "Enter data",
        # so the script can replace an existing _Measures table (Power BI does not
        # allow changing a partition from M to calculated or vice versa).
        "\t\tcolumn Value",
        "\t\t\tdataType: int64",
        "\t\t\tisHidden",
        "\t\t\tsummarizeBy: none",
        "\t\t\tsourceColumn: Value",
        "",
        "\t\tpartition _Measures = m",
        "\t\t\tmode: import",
        "\t\t\tsource = #table(type table [Value = Int64.Type], {})",
        "",
    ]
    return "\n".join(out)


def main() -> None:
    measures = parse_dax(DAX_FILE.read_text(encoding="utf-8"))
    TMDL_FILE.write_text(to_tmdl(measures), encoding="utf-8", newline="\n")
    print(f"{len(measures)} measures -> {TMDL_FILE.relative_to(cfg.ROOT_DIR)}")


if __name__ == "__main__":
    main()
