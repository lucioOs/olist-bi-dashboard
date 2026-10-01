"""
Render the data-quality results as a Markdown report, a CSV and figures.

Outputs
-------
reports/data_quality_report.md       human-readable report (GitHub renders it)
reports/data_quality_checks.csv      every check, machine-readable
reports/figures/dq_monthly_orders.png
reports/figures/dq_missing_values.png
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering (CI, servers)
import matplotlib.pyplot as plt
import pandas as pd

from src import config as cfg
from src.validate import monthly_orders

logger = logging.getLogger(__name__)

# Chart tokens (light theme, validated reference palette)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SERIES_1 = "#2a78d6"
MUTED = "#b9b8b3"


def _style_axes(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def plot_monthly_orders(tables: dict[str, pd.DataFrame], path: Path) -> Path:
    mo = monthly_orders(tables)
    labels = mo.index.strftime("%Y-%m")
    colors = [MUTED if inc else SERIES_1 for inc in mo["incomplete"]]

    fig, ax = plt.subplots(figsize=(11, 4.2), facecolor=SURFACE)
    x = range(len(mo))
    ax.bar(x, mo["orders"], color=colors, width=0.72)
    ax.set_xticks(list(x), labels)
    _style_axes(ax)
    ax.set_title("Orders per purchase month", loc="left", color=INK,
                 fontsize=13, fontweight="bold", pad=22)
    ax.text(0, 1.03, "Gray = incomplete month "
            f"(< {cfg.INCOMPLETE_MONTH_THRESHOLD:.0%} of the median month), "
            "excluded from trend and MoM analysis",
            transform=ax.transAxes, color=INK_2, fontsize=9)
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    plt.setp(ax.get_xticklabels(), rotation=90)
    for x, (n, inc) in enumerate(zip(mo["orders"], mo["incomplete"])):
        if inc:  # label only the months that need explanation
            ax.annotate(f"{n:,}", (x, n), xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=8, color=INK_2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def plot_missing(tables: dict[str, pd.DataFrame], path: Path) -> Path:
    rows = [(f"{t}.{c}", 100 * n / len(df))
            for t, df in tables.items() for c, n in df.isna().sum().items() if n]
    miss = pd.DataFrame(rows, columns=["column", "pct"]).sort_values("pct")

    fig, ax = plt.subplots(figsize=(9, 0.32 * len(miss) + 1.4), facecolor=SURFACE)
    y = range(len(miss))
    ax.barh(y, miss["pct"], color=SERIES_1, height=0.66)
    ax.set_yticks(list(y), miss["column"])
    _style_axes(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_title("Missing values by column (% of rows)", loc="left", color=INK,
                 fontsize=13, fontweight="bold")
    ax.set_xlim(0, 100)
    for y, p in enumerate(miss["pct"]):
        ax.text(p + 1, y, f"{p:.2f}%", va="center", fontsize=8, color=INK_2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
STATUS_ICON = {"PASS": "PASS", "INFO": "INFO", "WARNING": "WARN", "CRITICAL": "FAIL"}


def _md_table(df: pd.DataFrame) -> str:
    return df.to_markdown(index=False, floatfmt=",.2f", intfmt=",")


def build_markdown(checks: pd.DataFrame, profile: pd.DataFrame,
                   tables: dict[str, pd.DataFrame]) -> str:
    n = len(checks)
    counts = checks["status"].value_counts()
    passed = int(counts.get("PASS", 0))
    mo = monthly_orders(tables)
    complete = mo[~mo["incomplete"]]

    issues = checks[checks["status"] != "PASS"].copy()
    issues["status"] = issues["status"].map(STATUS_ICON)

    lines = [
        "# Data Quality Report - Olist Brazilian E-Commerce",
        "",
        f"*Generated automatically by `python -m src.main` on "
        f"{datetime.now():%Y-%m-%d %H:%M}. Source: [Kaggle]({cfg.KAGGLE_URL}).*",
        "",
        "## 1. Summary",
        "",
        f"- **{len(tables)} tables**, **{profile['rows'].sum():,} rows** in total.",
        f"- **{n} checks** run across 7 data-quality dimensions: "
        f"**{passed} passed**, {int(counts.get('CRITICAL', 0))} critical, "
        f"{int(counts.get('WARNING', 0))} warnings, {int(counts.get('INFO', 0))} informational.",
        f"- Reliable analysis window: **{complete.index.min()} to {complete.index.max()}** "
        f"({len(complete)} complete months).",
        "",
        "**Severity scale.** `FAIL` breaks the data model or a KPI if left untreated. "
        "`WARN` biases a metric and needs a documented decision. "
        "`INFO` is a property of the data that the model must respect.",
        "",
        "## 2. Table profile",
        "",
        _md_table(profile),
        "",
        "## 3. Findings (checks that did not pass)",
        "",
        _md_table(issues[["status", "dimension", "table", "check",
                          "n_affected", "n_total", "pct", "detail"]]
                  .rename(columns={"pct": "% affected"})),
        "",
        "## 4. Recommended treatment (specification for `clean.py`)",
        "",
        _md_table(issues[["status", "table", "check", "action"]]),
        "",
        "## 5. Temporal coverage",
        "",
        "![Orders per month](figures/dq_monthly_orders.png)",
        "",
        "## 6. Missing values",
        "",
        "![Missing values](figures/dq_missing_values.png)",
        "",
        "## 7. Checks that passed",
        "",
        _md_table(checks.loc[checks["status"] == "PASS",
                             ["dimension", "table", "check", "n_total"]]),
        "",
        "## 8. Methodology",
        "",
        "- **Outliers:** Tukey fences (Q1 - 1.5 IQR, Q3 + 1.5 IQR). Money and weight "
        "variables are right-skewed, so fences are computed on `log1p(x)`; a linear "
        "IQR would flag thousands of legitimate purchases. Outliers are **flagged, "
        "not removed**: an expensive product is a real sale.",
        "- **Incomplete months:** months with fewer orders than "
        f"{cfg.INCOMPLETE_MONTH_THRESHOLD:.0%} of the median month.",
        "- **Payments reconciliation:** per order, `sum(payment_value)` vs "
        f"`sum(price + freight_value)` with a {cfg.PAYMENT_TOLERANCE_BRL} BRL tolerance.",
        "- All rules are declared in `src/config.py`; the validator never modifies data.",
        "",
    ]
    return "\n".join(lines)


def write_report(checks: pd.DataFrame, profile: pd.DataFrame,
                 tables: dict[str, pd.DataFrame]) -> Path:
    cfg.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    plot_monthly_orders(tables, cfg.FIGURES_DIR / "dq_monthly_orders.png")
    plot_missing(tables, cfg.FIGURES_DIR / "dq_missing_values.png")

    checks.to_csv(cfg.REPORTS_DIR / "data_quality_checks.csv", index=False)
    md_path = cfg.REPORTS_DIR / "data_quality_report.md"
    md_path.write_text(build_markdown(checks, profile, tables), encoding="utf-8")
    logger.info("Report written to %s", md_path.relative_to(cfg.ROOT_DIR))
    return md_path
