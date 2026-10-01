"""The TMDL script must contain every measure of measures.dax, with a folder and format."""
from src.powerbi_tmdl import DAX_FILE, parse_dax, to_tmdl


def test_all_measures_parsed():
    ms = parse_dax(DAX_FILE.read_text(encoding="utf-8"))
    assert len(ms) == 46
    assert len({m.name for m in ms}) == 46            # no duplicated names
    assert all(m.folder and m.lines for m in ms)


def test_tmdl_structure():
    ms = parse_dax(DAX_FILE.read_text(encoding="utf-8"))
    tmdl = to_tmdl(ms)
    assert tmdl.startswith("createOrReplace")
    assert tmdl.count("\t\tmeasure '") == 46
    assert "formatString: 0.0%" in tmdl and "partition _Measures = m" in tmdl
