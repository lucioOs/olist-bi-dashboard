"""
Load the 9 raw Olist CSV files into typed pandas DataFrames.

Design decisions
----------------
* Schemas come from `config.TABLES`, so types are explicit and reproducible
  (no silent float->object inference differences between machines).
* Zip-code prefixes are read as strings and zero-padded to 5 chars.
  Read as integers, "01001" becomes 1001 and joins against geolocation break.
* Dates are parsed with a fixed format and `errors="coerce"`; the number of
  values that could NOT be parsed is stored in `df.attrs` so validate.py can
  report it instead of hiding it.
* An optional Parquet cache in data/interim makes re-runs ~10x faster.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import INTERIM_DIR, KAGGLE_URL, RAW_DIR, TABLES, TableSpec

logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class SchemaError(ValueError):
    """Raised when a raw file does not match its expected schema."""


def _check_files_exist(raw_dir: Path) -> None:
    """Fail fast with an actionable message if any raw file is missing."""
    missing = [spec.file for spec in TABLES.values() if not (raw_dir / spec.file).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing {len(missing)} raw file(s) in {raw_dir}:\n  - "
            + "\n  - ".join(missing)
            + f"\nDownload the dataset from {KAGGLE_URL} and unzip it into data/raw/."
        )


def _parse_dates(df: pd.DataFrame, cols: tuple[str, ...]) -> dict[str, int]:
    """Parse date columns in place. Returns {column: n_unparseable_values}."""
    unparseable: dict[str, int] = {}
    for col in cols:
        raw = df[col]
        parsed = pd.to_datetime(raw, format=DATE_FORMAT, errors="coerce")
        # Values that existed in the raw file but became NaT are parsing failures
        unparseable[col] = int((raw.notna() & parsed.isna()).sum())
        df[col] = parsed
    return unparseable


def load_table(spec: TableSpec, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Read one CSV according to its TableSpec and return a typed DataFrame."""
    path = raw_dir / spec.file

    # utf-8-sig strips the BOM present in some versions of the translation file
    df = pd.read_csv(path, encoding="utf-8-sig", dtype="string")
    df.columns = df.columns.str.strip()

    missing_cols = set(spec.columns) - set(df.columns)
    if missing_cols:
        raise SchemaError(f"{spec.file}: missing columns {sorted(missing_cols)}")
    extra_cols = set(df.columns) - set(spec.columns)
    if extra_cols:
        logger.warning("%s: unexpected extra columns %s (kept)", spec.file, sorted(extra_cols))

    # Trim whitespace in text columns (read as string, so all columns qualify)
    for col in df.columns:
        df[col] = df[col].str.strip()

    for col in spec.zip_cols:
        df[col] = df[col].str.zfill(5)

    unparseable = _parse_dates(df, spec.date_cols)

    # Cast non-date columns. Numeric casts go through to_numeric to count failures.
    for col, dtype in spec.dtypes.items():
        if dtype in ("string", "category"):
            df[col] = df[col].astype(dtype)
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        unparseable[col] = int((df[col].notna() & numeric.isna()).sum())
        # Nullable integer casts fail on non-integral floats; round only if exact
        df[col] = numeric.astype(dtype) if dtype.startswith("float") else numeric.round().astype(dtype)

    df.attrs["source_file"] = spec.file
    df.attrs["unparseable"] = {k: v for k, v in unparseable.items() if v}
    logger.info("Loaded %-22s %9s rows x %2d cols", spec.name, f"{len(df):,}", df.shape[1])
    return df


def load_all(raw_dir: Path = RAW_DIR, use_cache: bool = False) -> dict[str, pd.DataFrame]:
    """
    Load every table defined in config.TABLES.

    Parameters
    ----------
    raw_dir : folder with the original Kaggle CSVs.
    use_cache : if True, read/write typed Parquet copies in data/interim.
    """
    _check_files_exist(raw_dir)
    tables: dict[str, pd.DataFrame] = {}

    for name, spec in TABLES.items():
        cache = INTERIM_DIR / f"{name}.parquet"
        raw_mtime = (raw_dir / spec.file).stat().st_mtime
        if use_cache and cache.exists() and cache.stat().st_mtime >= raw_mtime:
            df = pd.read_parquet(cache)
            logger.info("Loaded %-22s from cache", name)
        else:
            df = load_table(spec, raw_dir)
            if use_cache:
                INTERIM_DIR.mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache, index=False)
        tables[name] = df

    return tables


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    for name, df in load_all().items():
        print(f"{name:<22} {df.shape}")
