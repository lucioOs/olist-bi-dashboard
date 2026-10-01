"""
Pipeline entry point.

Usage (from the repository root):
    python -m src.main              # load -> validate -> clean -> model -> export
    python -m src.main --no-cache   # force re-reading the raw CSVs
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from src import config as cfg
from src.clean import clean_all
from src.export import export_csv, write_cleaning_log, write_data_model_doc
from src.load import load_all
from src.model import build_model
from src.report import write_report
from src.validate import profile_tables, run_all_checks


def setup_logging() -> None:
    cfg.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(cfg.LOG_DIR / "pipeline.log", encoding="utf-8")],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Olist BI data pipeline")
    parser.add_argument("--no-cache", action="store_true", help="ignore Parquet cache")
    args = parser.parse_args(argv)

    setup_logging()
    log = logging.getLogger("pipeline")
    t0 = time.perf_counter()

    log.info("Stage 1/4 - load raw tables")
    raw = load_all(use_cache=not args.no_cache)

    log.info("Stage 2/4 - validate and write data-quality report")
    checks = run_all_checks(raw)
    write_report(checks, profile_tables(raw), raw)

    log.info("Stage 3/4 - clean")
    clean, cleaning_log = clean_all(raw)
    write_cleaning_log(cleaning_log)

    log.info("Stage 4/4 - build star schema, check integrity, export")
    model, model_checks = build_model(clean, raw)
    export_csv(model)
    write_data_model_doc(model, model_checks)

    summary = checks["status"].value_counts().to_dict()
    log.info("Quality checks: %s | model integrity: %d/%d PASS | done in %.1fs",
             summary, (model_checks["result"] == "PASS").sum(), len(model_checks),
             time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
