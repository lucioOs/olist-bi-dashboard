"""
Data-quality validation for the raw Olist tables.

The module does NOT modify data. It runs a battery of checks grouped by the
standard data-quality dimensions and returns one `CheckResult` per check:

    Completeness  - missing values, parent rows without children
    Uniqueness    - primary keys, full-row duplicates, grain assumptions
    Validity      - allowed categorical values, numeric ranges, coordinates, parsing
    Consistency   - date chronology, status vs. dates, payments vs. items
    Integrity     - foreign keys (orphans)
    Accuracy      - statistical outliers (IQR, log scale for skewed money data)
    Timeliness    - monthly coverage / incomplete months

Every finding carries a severity and a recommended action, which becomes the
specification for `clean.py`. Results are exported to
reports/data_quality_report.md and reports/data_quality_checks.csv.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from src import config as cfg

logger = logging.getLogger(__name__)

CRITICAL, WARNING, INFO = "CRITICAL", "WARNING", "INFO"
SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    dimension: str
    table: str
    check: str
    severity: str          # severity IF the check fails
    n_affected: int
    n_total: int
    detail: str = ""
    action: str = ""       # recommended treatment in clean.py

    @property
    def pct(self) -> float:
        return 100 * self.n_affected / self.n_total if self.n_total else 0.0

    @property
    def status(self) -> str:
        return "PASS" if self.n_affected == 0 else self.severity

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(pct=round(self.pct, 4), status=self.status)
        return d


# --------------------------------------------------------------------------- #
# Completeness
# --------------------------------------------------------------------------- #
def check_nulls(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """One result per column that has at least one missing value."""
    results = []
    for name, df in tables.items():
        na = df.isna()
        for col, n_null in na.sum().items():
            if n_null == 0:
                continue
            expected = cfg.EXPECTED_NULLS.get((name, col))
            # Columns that are ALWAYS missing in the same rows -> one root cause
            rows = na[col]
            together = [c for c in df.columns if c != col and na.loc[rows, c].all()]
            results.append(CheckResult(
                dimension="Completeness", table=name, check=f"Missing values: {col}",
                severity=INFO if expected else WARNING,
                n_affected=int(n_null), n_total=len(df),
                detail=(expected or "Unexpected missing values")
                + (f". Always missing together with: {', '.join(together)}" if together else ""),
                action="Keep (expected by business logic)" if expected
                else "Impute, label as 'unknown' or exclude from affected metrics",
            ))
    return results


def check_coverage(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """Parent rows with no matching child (e.g. orders without items)."""
    results = []
    for parent, pcol, child, ccol in cfg.COVERAGE_CHECKS:
        p = tables[parent]
        missing = ~p[pcol].isin(tables[child][ccol])
        results.append(CheckResult(
            dimension="Completeness", table=parent,
            check=f"{parent} without {child}",
            severity=WARNING, n_affected=int(missing.sum()), n_total=len(p),
            detail=_status_breakdown(p.loc[missing]) if parent == "orders" else "",
            action=f"Exclude from metrics that depend on {child}",
        ))
    return results


# --------------------------------------------------------------------------- #
# Uniqueness
# --------------------------------------------------------------------------- #
def check_primary_keys(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for name, spec in cfg.TABLES.items():
        if not spec.primary_key:
            continue
        df = tables[name]
        dup = df.duplicated(subset=list(spec.primary_key), keep=False)
        null_key = df[list(spec.primary_key)].isna().any(axis=1)
        results.append(CheckResult(
            dimension="Uniqueness", table=name,
            check=f"Primary key unique & not null ({', '.join(spec.primary_key)})",
            severity=CRITICAL, n_affected=int((dup | null_key).sum()), n_total=len(df),
            action="Deduplicate before modelling; a broken key breaks Power BI relationships",
        ))
    return results


def check_full_duplicates(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for name, df in tables.items():
        n_dup = int(df.duplicated(keep="first").sum())
        results.append(CheckResult(
            dimension="Uniqueness", table=name, check="Exact duplicate rows",
            severity=WARNING if name != "geolocation" else INFO,
            n_affected=n_dup, n_total=len(df),
            detail="Many GPS samples per zip prefix" if name == "geolocation" else "",
            action="Drop exact duplicates",
        ))
    return results


def check_grain(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """Verify modelling assumptions about how many rows exist per key."""
    results = []
    for name, col, why in cfg.GRAIN_CHECKS:
        df = tables[name]
        counts = df[col].value_counts()
        repeated = counts[counts > 1]
        results.append(CheckResult(
            dimension="Uniqueness", table=name, check=f"Rows per {col} > 1",
            severity=INFO, n_affected=int(len(repeated)), n_total=int(len(counts)),
            detail=f"{why}. Max rows for one key: {int(counts.max())}",
            action="Handle explicitly in the data model",
        ))
    return results


# --------------------------------------------------------------------------- #
# Integrity
# --------------------------------------------------------------------------- #
def check_foreign_keys(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for child, ccol, parent, pcol in cfg.FOREIGN_KEYS:
        c = tables[child]
        values = c[ccol].dropna()
        orphans = ~values.isin(tables[parent][pcol])
        orphan_values = values[orphans]
        sample = ", ".join(map(str, orphan_values.unique()[:5]))
        results.append(CheckResult(
            dimension="Integrity", table=child,
            check=f"{child}.{ccol} -> {parent}.{pcol}",
            # Missing zip coordinates only affect maps -> not critical
            severity=WARNING if parent in ("geolocation", "category_translation") else CRITICAL,
            n_affected=int(orphans.sum()), n_total=len(values),
            detail=(f"{orphan_values.nunique()} distinct orphan values. e.g. {sample}"
                    if orphans.any() else ""),
            action="Add missing parent rows or map to an 'unknown' member",
        ))
    return results


# --------------------------------------------------------------------------- #
# Validity
# --------------------------------------------------------------------------- #
def check_allowed_values(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for (name, col), allowed in cfg.ALLOWED_VALUES.items():
        s = tables[name][col].dropna().astype(str)
        invalid = ~s.isin(allowed)
        results.append(CheckResult(
            dimension="Validity", table=name, check=f"Allowed values: {col}",
            severity=WARNING, n_affected=int(invalid.sum()), n_total=len(s),
            detail=f"Unexpected: {sorted(s[invalid].unique())[:10]}" if invalid.any() else "",
            action="Map to a valid value or 'unknown'",
        ))
    return results


def check_numeric_ranges(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for name, col, lo, hi, incl_lo in cfg.NUMERIC_RANGES:
        s = tables[name][col].dropna().astype(float)
        bad = pd.Series(False, index=s.index)
        if lo is not None:
            bad |= (s < lo) if incl_lo else (s <= lo)
        if hi is not None:
            bad |= s > hi
        rng = f"{'[' if incl_lo else '('}{lo}, {hi if hi is not None else 'inf'}]"
        results.append(CheckResult(
            dimension="Validity", table=name, check=f"Range {col} in {rng}",
            severity=WARNING, n_affected=int(bad.sum()), n_total=len(s),
            detail=("Offending values: " + ", ".join(f"{x:g}" for x in sorted(s[bad].unique())[:8])
                    if bad.any() else ""),
            action="Set to NULL (unknown) or correct if the rule is obvious",
        ))
    return results


def check_coordinates(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    g = tables["geolocation"]
    b = cfg.BRAZIL_BBOX
    outside = ~(g["geolocation_lat"].between(b["lat_min"], b["lat_max"])
                & g["geolocation_lng"].between(b["lng_min"], b["lng_max"]))
    return [CheckResult(
        dimension="Validity", table="geolocation",
        check="Coordinates inside Brazil bounding box",
        severity=WARNING, n_affected=int(outside.sum()), n_total=len(g),
        action="Drop before averaging coordinates per zip prefix",
    )]


def check_parsing(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """Values present in the CSV that could not be parsed to the target type."""
    results = []
    for name, df in tables.items():
        for col, n in df.attrs.get("unparseable", {}).items():
            results.append(CheckResult(
                dimension="Validity", table=name, check=f"Unparseable values: {col}",
                severity=CRITICAL, n_affected=int(n), n_total=len(df),
                action="Inspect raw file; fix format or set to NULL",
            ))
    if not results:
        results.append(CheckResult(
            dimension="Validity", table="all", check="All values parsed to target types",
            severity=CRITICAL, n_affected=0, n_total=sum(len(d) for d in tables.values()),
        ))
    return results


# --------------------------------------------------------------------------- #
# Consistency
# --------------------------------------------------------------------------- #
def check_date_order(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for name, early, late, desc in cfg.DATE_ORDER_RULES:
        df = tables[name]
        both = df[early].notna() & df[late].notna()
        bad = both & (df[late] < df[early])
        detail = ""
        if bad.any():
            gap_h = (df.loc[bad, early] - df.loc[bad, late]).dt.total_seconds() / 3600
            detail = f"Median gap {gap_h.median():.1f} h, max {gap_h.max():.1f} h"
        results.append(CheckResult(
            dimension="Consistency", table=name, check=f"{desc} ({late} < {early})",
            severity=WARNING, n_affected=int(bad.sum()), n_total=int(both.sum()),
            detail=detail,
            action="Flag; exclude from duration metrics that use this pair of dates",
        ))
    return results


def check_status_vs_dates(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    o = tables["orders"]
    delivered = o["order_status"] == "delivered"
    has_date = o["order_delivered_customer_date"].notna()
    return [
        CheckResult(
            dimension="Consistency", table="orders",
            check="Status 'delivered' without delivery date",
            severity=WARNING, n_affected=int((delivered & ~has_date).sum()),
            n_total=int(delivered.sum()),
            action="Exclude from delivery-time and on-time metrics",
        ),
        CheckResult(
            dimension="Consistency", table="orders",
            check="Delivery date present but status is not 'delivered'",
            severity=WARNING, n_affected=int((~delivered & has_date).sum()),
            n_total=int(has_date.sum()),
            detail=_status_breakdown(o.loc[~delivered & has_date]),
            action="Keep status as source of truth; document",
        ),
    ]


def check_shipping_limit(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """Seller shipping deadline must be after purchase and within a plausible window."""
    it = tables["order_items"].merge(
        tables["orders"][["order_id", "order_purchase_timestamp"]], on="order_id", how="inner")
    days = (it["shipping_limit_date"] - it["order_purchase_timestamp"]).dt.days
    bad = (days < 0) | (days > cfg.MAX_SHIPPING_LIMIT_DAYS)
    return [CheckResult(
        dimension="Consistency", table="order_items",
        check=f"shipping_limit_date within 0-{cfg.MAX_SHIPPING_LIMIT_DAYS} days of purchase",
        severity=WARNING, n_affected=int(bad.sum()), n_total=int(days.notna().sum()),
        detail=(f"Median {days.median():.0f} days; max {days.max():.0f} days" if len(days) else ""),
        action="Set shipping_limit_date to NULL for implausible values",
    )]


def check_payments_vs_items(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """Reconcile what the customer paid with what the items cost."""
    items = (tables["order_items"]
             .assign(item_total=lambda d: d["price"] + d["freight_value"])
             .groupby("order_id")["item_total"].sum())
    paid = tables["order_payments"].groupby("order_id")["payment_value"].sum()
    both = pd.concat([items.rename("items"), paid.rename("paid")], axis=1, join="inner")
    diff = both["paid"] - both["items"]
    bad = diff.abs() > cfg.PAYMENT_TOLERANCE_BRL
    return [CheckResult(
        dimension="Consistency", table="order_payments",
        check=f"sum(payments) = sum(price + freight) +/- {cfg.PAYMENT_TOLERANCE_BRL} BRL",
        severity=INFO, n_affected=int(bad.sum()), n_total=len(both),
        detail=(f"Overpaid: {int((diff > cfg.PAYMENT_TOLERANCE_BRL).sum())} "
                f"(likely installment interest), underpaid: "
                f"{int((diff < -cfg.PAYMENT_TOLERANCE_BRL).sum())} (likely vouchers). "
                f"Median |diff| among flagged: {diff[bad].abs().median():.2f} BRL"),
        action="Define Revenue from order_items (price), not payments",
    )]


# --------------------------------------------------------------------------- #
# Accuracy (statistical outliers)
# --------------------------------------------------------------------------- #
def iqr_bounds(s: pd.Series, k: float = cfg.IQR_MULTIPLIER, log: bool = False
               ) -> tuple[float, float]:
    """Tukey fences. With log=True fences are computed on log1p(x) and mapped back."""
    x = np.log1p(s) if log else s
    q1, q3 = x.quantile([0.25, 0.75])
    lo, hi = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
    return (float(np.expm1(lo)), float(np.expm1(hi))) if log else (float(lo), float(hi))


def check_outliers(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    results = []
    for name, col, log in cfg.OUTLIER_COLUMNS:
        s = tables[name][col].dropna().astype(float)
        s = s[s >= 0]
        lo, hi = iqr_bounds(s, log=log)
        out = (s < lo) | (s > hi)
        results.append(CheckResult(
            dimension="Accuracy", table=name,
            check=f"IQR outliers: {col}" + (" (log scale)" if log else ""),
            severity=INFO, n_affected=int(out.sum()), n_total=len(s),
            detail=(f"Fences [{max(lo, 0):,.2f}, {hi:,.2f}]; "
                    f"p50={s.median():,.2f}, p99={s.quantile(.99):,.2f}, max={s.max():,.2f}"),
            action="Keep (real high-value items); flag and cap only in visuals if needed",
        ))

    # Business-rule outlier: freight more expensive than the product itself
    it = tables["order_items"]
    fr = it["freight_value"] > it["price"]
    results.append(CheckResult(
        dimension="Accuracy", table="order_items", check="Freight greater than item price",
        severity=INFO, n_affected=int(fr.sum()), n_total=len(it),
        detail="Relevant for logistics-cost analysis (cheap, heavy items)",
        action="Keep; create flag column freight_gt_price",
    ))
    return results


# --------------------------------------------------------------------------- #
# Timeliness
# --------------------------------------------------------------------------- #
def monthly_orders(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Orders per purchase month, flagging months too small to be trusted."""
    ts = tables["orders"]["order_purchase_timestamp"].dropna()
    m = ts.dt.to_period("M").value_counts().sort_index()
    # Fill gaps so missing months are visible
    m = m.reindex(pd.period_range(m.index.min(), m.index.max(), freq="M"), fill_value=0)
    out = m.rename("orders").to_frame()
    out["share_of_median"] = out["orders"] / out["orders"].median()
    out["incomplete"] = out["share_of_median"] < cfg.INCOMPLETE_MONTH_THRESHOLD
    return out


def check_timeliness(tables: dict[str, pd.DataFrame]) -> list[CheckResult]:
    mo = monthly_orders(tables)
    bad = mo[mo["incomplete"]]
    ts = tables["orders"]["order_purchase_timestamp"]
    return [CheckResult(
        dimension="Timeliness", table="orders",
        check=f"Months with < {cfg.INCOMPLETE_MONTH_THRESHOLD:.0%} of median monthly orders",
        severity=WARNING, n_affected=len(bad), n_total=len(mo),
        detail=(f"Range {ts.min():%Y-%m-%d} to {ts.max():%Y-%m-%d}. Incomplete: "
                + ", ".join(f"{p} ({n})" for p, n in bad["orders"].items())),
        action="Restrict trend and MoM % analysis to complete months",
    )]


# --------------------------------------------------------------------------- #
# Helpers & orchestration
# --------------------------------------------------------------------------- #
def _status_breakdown(orders: pd.DataFrame) -> str:
    if orders.empty:
        return ""
    vc = orders["order_status"].astype(str).value_counts()
    return "By status: " + ", ".join(f"{k}={v}" for k, v in vc.items())


CHECKS = [
    check_parsing, check_nulls, check_coverage,
    check_primary_keys, check_full_duplicates, check_grain,
    check_foreign_keys,
    check_allowed_values, check_numeric_ranges, check_coordinates,
    check_date_order, check_status_vs_dates, check_shipping_limit,
    check_payments_vs_items,
    check_outliers, check_timeliness,
]


def run_all_checks(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Run every check and return a tidy DataFrame sorted by severity."""
    results: list[CheckResult] = []
    for fn in CHECKS:
        try:
            results.extend(fn(tables))
        except Exception as exc:  # one broken check must not hide the rest
            logger.exception("Check %s failed", fn.__name__)
            results.append(CheckResult("Pipeline", "-", fn.__name__, CRITICAL, 1, 1,
                                       detail=f"Check crashed: {exc}"))
    df = pd.DataFrame([r.to_dict() for r in results])
    df["_order"] = df["status"].map({**SEVERITY_ORDER, "PASS": 3})
    return (df.sort_values(["_order", "dimension", "table", "pct"],
                           ascending=[True, True, True, False])
              .drop(columns="_order").reset_index(drop=True))


def profile_tables(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """High-level profile: size, memory, null share and date range per table."""
    rows = []
    for name, df in tables.items():
        spec = cfg.TABLES[name]
        dates = df[list(spec.date_cols)] if spec.date_cols else None
        rows.append({
            "table": name,
            "file": spec.file,
            "rows": len(df),
            "columns": df.shape[1],
            "memory_mb": round(df.memory_usage(deep=True).sum() / 1e6, 1),
            "null_cells_pct": round(100 * df.isna().to_numpy().mean(), 2),
            "date_range": (f"{dates.min().min():%Y-%m-%d} to {dates.max().max():%Y-%m-%d}"
                           if dates is not None else "-"),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    from src.load import load_all

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    res = run_all_checks(load_all(use_cache=True))
    print(res[["status", "table", "check", "n_affected", "pct"]].to_string())
    print(f"\nGenerated {datetime.now():%Y-%m-%d %H:%M}")
