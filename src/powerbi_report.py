"""
Report-as-code: build Power BI report pages (PBIR format) from a Python spec.

Usage:
    python -m src.powerbi_report        # writes the visuals of the pages defined below

Power BI Desktop must be CLOSED while this runs (Desktop does not watch the
files and would overwrite them on its next save). Commit first: git is the
rollback if a visual definition needs fixing.

Coordinates are for the 1920 x 1080 canvas (16:9). Layout grid: 24 px outer
margin, 24 px gutter. See powerbi/dashboard_spec.md for the design contract.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src import config as cfg

REPORT_DIR = cfg.ROOT_DIR / "powerbi" / "olist_dashboard.Report" / "definition"
VISUAL_SCHEMA = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
                 "definition/visualContainer/2.13.0/schema.json")

INK_2 = "#52514E"


# --------------------------------------------------------------------------- #
# Small builders for the PBIR JSON grammar
# --------------------------------------------------------------------------- #
def lit(value) -> dict:
    """Literal expression. Strings are quoted DAX-style ('text'), bools lower-case."""
    if isinstance(value, bool):
        v = "true" if value else "false"
    elif isinstance(value, (int, float)):
        v = f"{value}D"
    else:
        v = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": v}}}


def column(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def measure(prop: str, entity: str = "_Measures") -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def projection(field: dict, display_name: str | None = None) -> dict:
    kind = "Column" if "Column" in field else "Measure"
    entity = field[kind]["Expression"]["SourceRef"]["Entity"]
    prop = field[kind]["Property"]
    p = {"field": field, "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if display_name:
        p["displayName"] = display_name
    return p


def sort_by(field: dict, direction: str = "Descending") -> dict:
    return {"sort": [{"field": field, "direction": direction}]}


def title(text: str, subtitle: str | None = None) -> dict:
    objs = {"title": [{"properties": {"show": lit(True), "text": lit(text)}}]}
    if subtitle:
        objs["subTitle"] = [{"properties": {"show": lit(True), "text": lit(subtitle)}}]
    return objs


def bool_filter(entity: str, prop: str, value: bool, name: str) -> dict:
    """Visual-level filter <entity>[<prop>] = value (e.g. complete months only)."""
    alias = entity[0]
    return {
        "name": name,
        "field": column(entity, prop),
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": alias, "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": alias}},
                                            "Property": prop}}],
                "Values": [[{"Literal": {"Value": "true" if value else "false"}}]],
            }}}],
        },
        "howCreated": "User",
    }


def text_run(value: str, size: int, bold: bool = False, color: str | None = None) -> dict:
    style: dict = {"fontSize": f"{size}pt"}
    if bold:
        style["fontWeight"] = "bold"
    if color:
        style["color"] = color
    return {"value": value, "textStyle": style}


def visual_id(page: str, key: str) -> str:
    """Stable 20-hex id, so re-running the generator overwrites instead of duplicating."""
    return hashlib.sha1(f"{page}/{key}".encode()).hexdigest()[:20]


def container(name: str, x, y, w, h, z: int, visual: dict,
              filters: list[dict] | None = None) -> dict:
    c = {
        "$schema": VISUAL_SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z},
        "visual": visual,
    }
    if filters:
        c["filterConfig"] = {"filters": filters}
    return c


# --------------------------------------------------------------------------- #
# Visual factories
# --------------------------------------------------------------------------- #
def textbox(paragraphs: list[list[dict]]) -> dict:
    return {"visualType": "textbox", "objects": {"general": [{"properties": {
        "paragraphs": [{"textRuns": runs} for runs in paragraphs]}}]}}


def slicer(field: dict, label: str, mode: str) -> dict:
    """mode: 'Between' (date range) or 'Dropdown' (multi-select list)."""
    objects = {"data": [{"properties": {"mode": lit(mode)}}]}
    if mode == "Dropdown":
        objects["selection"] = [{"properties": {
            "singleSelect": lit(False), "selectAllCheckboxEnabled": lit(True)}}]
    return {
        "visualType": "slicer",
        "query": {"queryState": {"Values": {"projections": [projection(field, label)]}}},
        "objects": objects,
        "drillFilterOtherVisuals": True,
    }


def kpi_card(measures: list[str]) -> dict:
    return {
        "visualType": "cardVisual",
        "query": {"queryState": {"Data": {"projections": [projection(measure(m)) for m in measures]}}},
        "visualContainerObjects": {"title": [{"properties": {"show": lit(False)}}]},
        "drillFilterOtherVisuals": True,
    }


def xy_chart(kind: str, category: dict, value: dict, chart_title: str,
             sort: dict | None = None, labels: bool = False,
             subtitle: str | None = None) -> dict:
    v = {
        "visualType": kind,
        "query": {"queryState": {
            "Category": {"projections": [projection(category)]},
            "Y": {"projections": [projection(value)]},
        }},
        "visualContainerObjects": title(chart_title, subtitle),
        "drillFilterOtherVisuals": True,
    }
    if sort:
        v["query"]["sortDefinition"] = sort
    if labels:
        v["objects"] = {"labels": [{"properties": {"show": lit(True)}}]}
    return v


# --------------------------------------------------------------------------- #
# Page 1 - Executive Summary
# --------------------------------------------------------------------------- #
def page_executive(page: str) -> list[dict]:
    vid = lambda k: visual_id(page, k)  # noqa: E731
    revenue = measure("Revenue")
    return [
        container(vid("header"), 24, 12, 1200, 78, 0, textbox([
            [text_run("Executive Summary", 24, bold=True)],
            [text_run("Olist marketplace · Jan 2017 – Aug 2018 · Values in BRL (R$)", 12,
                      color=INK_2)],
        ])),
        container(vid("slicer_date"), 24, 96, 600, 72, 1000,
                  slicer(column("dim_date", "date"), "Purchase date", "Between")),
        container(vid("slicer_region"), 648, 96, 270, 72, 2000,
                  slicer(column("dim_customer", "region"), "Region", "Dropdown")),
        container(vid("slicer_state"), 942, 96, 270, 72, 3000,
                  slicer(column("dim_customer", "state"), "State", "Dropdown")),
        container(vid("slicer_category"), 1236, 96, 360, 72, 4000,
                  slicer(column("dim_product", "category"), "Category", "Dropdown")),
        container(vid("kpis"), 24, 186, 1872, 144, 5000, kpi_card([
            "Revenue", "Orders", "AOV", "On-Time Delivery %",
            "Avg Review Score", "Repeat Customer %"])),
        container(vid("revenue_trend"), 24, 354, 1110, 336, 6000,
                  xy_chart("lineChart", column("dim_date", "year_month"), revenue,
                           "Revenue grew through 2017 and plateaued in 2018",
                           sort=sort_by(column("dim_date", "year_month"), "Ascending"),
                           subtitle="Monthly revenue, complete months only"),
                  filters=[bool_filter("dim_date", "is_complete_month", True,
                                       vid("f_complete"))]),
        container(vid("top_categories"), 1158, 354, 738, 336, 7000,
                  xy_chart("barChart", column("dim_product", "category"), revenue,
                           "10 categories generate 62% of revenue",
                           sort=sort_by(revenue), labels=True,
                           subtitle="Revenue by product category")),
        container(vid("review_by_delivery"), 24, 714, 1110, 342, 8000,
                  xy_chart("columnChart", column("fact_orders", "delivery_bucket"),
                           measure("Avg Review Score"),
                           "Reviews collapse when orders arrive late",
                           sort=sort_by(column("fact_orders", "delivery_bucket"), "Ascending"),
                           labels=True,
                           subtitle="Average review score by delivery timing vs. promised date")),
        container(vid("insights"), 1158, 714, 738, 342, 9000, textbox([
            [text_run("Key insights", 14, bold=True)],
            [text_run("• Late deliveries drive dissatisfaction: 2.27 average review when late "
                      "vs 4.29 on time.", 12)],
            [text_run("• Only 3.0% of customers buy again: growth relies on acquisition.", 12)],
            [text_run("• Orders arrive ~11 days earlier than promised: delivery estimates are "
                      "overly conservative.", 12)],
            [text_run("• São Paulo generates 38% of revenue; the Northeast has the highest "
                      "late-delivery rates.", 12)],
        ])),
    ]


PAGES = {"executive": ("Executive Summary", page_executive)}


def write_page(page_id: str, key: str, out_root: Path = REPORT_DIR) -> list[Path]:
    display_name, factory = PAGES[key]
    page_dir = out_root / "pages" / page_id
    written = []
    for v in factory(page_id):
        path = page_dir / "visuals" / v["name"] / "visual.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    import sys
    page_id = sys.argv[1] if len(sys.argv) > 1 else "b565616a283c09277c0e"
    for p in write_page(page_id, "executive"):
        print(p.relative_to(cfg.ROOT_DIR))
