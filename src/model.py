"""
Star-schema builder for Power BI.

Why two fact tables
-------------------
The business questions live at two different grains:

* item grain  -> revenue by product, category and seller
* order grain -> delivery time, on-time %, review score, payments, repeat rate

Putting order-level measures on the item table repeats them once per item
(an order with 3 items would count its review 3 times). Payments and reviews
also have several rows per order. So:

    fact_order_items  (1 row per order item)  -> dim_product, dim_seller, dim_customer, dim_date
    fact_orders       (1 row per order)       -> dim_customer, dim_date

Both facts share dim_customer and dim_date, so one slicer filters both
(conformed dimensions). All relationships are one-to-many, single direction.

fact_order_items also carries a few order-context columns (review_score,
is_late...) so seller performance can be analysed without a bidirectional
relationship; DAX must aggregate them at order level (see docs).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src import config as cfg
from src.validate import monthly_orders

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Dimensions
# --------------------------------------------------------------------------- #
def build_dim_date(raw_tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Continuous calendar covering full years (required by DAX time intelligence)."""
    ts = raw_tables["orders"]["order_purchase_timestamp"]
    dates = pd.date_range(f"{ts.min().year}-01-01", f"{ts.max().year}-12-31", freq="D")
    d = pd.DataFrame({"date": dates})
    d["date_key"] = d["date"].dt.strftime("%Y%m%d").astype(int)
    d["year"] = d["date"].dt.year
    d["quarter"] = "Q" + d["date"].dt.quarter.astype(str)
    d["year_quarter"] = d["year"].astype(str) + "-" + d["quarter"]
    d["month_num"] = d["date"].dt.month
    d["month_name"] = d["date"].dt.strftime("%B")
    d["month_short"] = d["date"].dt.strftime("%b")
    d["year_month"] = d["date"].dt.strftime("%Y-%m")
    d["year_month_num"] = d["year"] * 100 + d["month_num"]       # sort key for year_month
    d["month_start"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["iso_week"] = d["date"].dt.isocalendar().week.astype(int)
    d["day_of_week_num"] = d["date"].dt.dayofweek + 1               # Monday = 1
    d["day_name"] = d["date"].dt.strftime("%A")
    d["is_weekend"] = d["day_of_week_num"] >= 6

    mo = monthly_orders(raw_tables)
    complete = {str(p) for p in mo.index[~mo["incomplete"]]}
    d["is_complete_month"] = d["year_month"].isin(complete)
    return d


def build_dim_customer(clean: dict[str, pd.DataFrame], fact_orders: pd.DataFrame) -> pd.DataFrame:
    c = clean["customers"].rename(columns={
        "customer_zip_code_prefix": "zip_code_prefix", "customer_city": "city",
        "customer_state": "state"})
    c["state"] = c["state"].astype(str)
    c["region"] = c["state"].map(cfg.STATE_REGION)

    sales = fact_orders[fact_orders["is_sale"]]
    stats = (sales.groupby("customer_unique_id")
                  .agg(first_purchase_date=("purchase_date", "min"),
                       last_purchase_date=("purchase_date", "max"),
                       lifetime_orders=("order_id", "count"),
                       lifetime_revenue=("items_price", "sum")))
    c = c.merge(stats, on="customer_unique_id", how="left")
    c["lifetime_orders"] = c["lifetime_orders"].fillna(0).astype(int)
    c["lifetime_revenue"] = c["lifetime_revenue"].fillna(0).round(2)
    c["is_repeat_customer"] = c["lifetime_orders"] >= 2
    return c[["customer_unique_id", "zip_code_prefix", "city", "state", "region",
              "lat", "lng", "geo_source", "first_purchase_date", "last_purchase_date",
              "lifetime_orders", "lifetime_revenue", "is_repeat_customer"]]


def build_dim_product(clean: dict[str, pd.DataFrame]) -> pd.DataFrame:
    p = clean["products"].rename(columns={"product_category_name": "category_pt"})
    return p[["product_id", "category", "category_pt", "product_weight_g",
              "product_length_cm", "product_height_cm", "product_width_cm",
              "product_volume_cm3", "product_photos_qty", "product_name_length",
              "product_description_length"]]


def build_dim_seller(clean: dict[str, pd.DataFrame]) -> pd.DataFrame:
    s = clean["sellers"].rename(columns={
        "seller_zip_code_prefix": "zip_code_prefix", "seller_city": "city",
        "seller_state": "state"})
    s["state"] = s["state"].astype(str)
    s["region"] = s["state"].map(cfg.STATE_REGION)
    return s[["seller_id", "zip_code_prefix", "city", "state", "region",
              "lat", "lng", "geo_source"]]


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #
def build_fact_orders(clean: dict[str, pd.DataFrame]) -> pd.DataFrame:
    o = clean["orders"].merge(clean["customer_map"], on="customer_id", how="left")

    items = (clean["order_items"].groupby("order_id")
             .agg(items_count=("order_item_id", "count"),
                  products_count=("product_id", "nunique"),
                  sellers_count=("seller_id", "nunique"),
                  items_price=("price", "sum"),
                  freight_total=("freight_value", "sum")))
    o = (o.merge(items, on="order_id", how="left")
          .merge(clean["payments"], on="order_id", how="left")
          .merge(clean["reviews"][["order_id", "review_score", "review_count",
                                   "has_comment", "review_response_hours"]],
                 on="order_id", how="left"))

    o["has_items"] = o["items_count"].notna()
    o["has_review"] = o["review_score"].notna()
    for col in ("items_count", "products_count", "sellers_count", "review_count"):
        o[col] = o[col].fillna(0).astype(int)
    for col in ("items_price", "freight_total"):
        o[col] = o[col].fillna(0.0).round(2)
    o["order_value"] = (o["items_price"] + o["freight_total"]).round(2)
    # Left joins introduce NaN -> restore integer/boolean semantics (nullable types)
    o["payment_methods"] = o["payment_methods"].astype("Int64")
    o["max_installments"] = o["max_installments"].astype("Int64")
    o["used_voucher"] = o["used_voucher"].astype("boolean")
    o["has_comment"] = o["has_comment"].astype("boolean")
    o["payment_total"] = o["payment_total"].round(2)
    o["is_multi_seller"] = o["sellers_count"] > 1
    o["is_installment"] = (o["max_installments"] > 1).astype("boolean")

    # Purchase sequence per PERSON, counting only real sales
    o = o.sort_values(["customer_unique_id", "order_purchase_timestamp"])
    seq = o[o["is_sale"]].groupby("customer_unique_id").cumcount() + 1
    o["customer_order_seq"] = seq.reindex(o.index).astype("Int64")
    o["is_repeat_purchase"] = (o["customer_order_seq"] > 1).astype("boolean")

    o["date_key"] = o["purchase_date"].dt.strftime("%Y%m%d").astype(int)
    cols = [
        # keys
        "order_id", "customer_unique_id", "date_key", "purchase_date",
        "order_purchase_timestamp", "order_status",
        # status flags
        "is_sale", "has_items", "is_delivered", "is_late", "is_multi_seller",
        "is_repeat_purchase", "customer_order_seq",
        # amounts
        "items_count", "products_count", "sellers_count",
        "items_price", "freight_total", "order_value",
        # payments
        "payment_total", "main_payment_type", "payment_methods", "max_installments",
        "is_installment", "used_voucher",
        # delivery
        "order_approved_at", "order_delivered_carrier_date",
        "order_delivered_customer_date", "order_estimated_delivery_date",
        "approval_hours", "carrier_lead_days", "last_mile_days", "delivery_days",
        "promised_days", "delay_days",
        "flag_carrier_before_approval", "flag_delivered_before_carrier",
        # satisfaction
        "has_review", "review_score", "review_count", "has_comment", "review_response_hours",
    ]
    return o[cols].sort_values("order_purchase_timestamp").reset_index(drop=True)


def build_fact_order_items(clean: dict[str, pd.DataFrame], fact_orders: pd.DataFrame) -> pd.DataFrame:
    ctx = fact_orders[["order_id", "customer_unique_id", "date_key", "purchase_date",
                       "order_status", "is_sale", "is_delivered", "is_late",
                       "delivery_days", "review_score", "is_multi_seller"]]
    it = clean["order_items"].merge(ctx, on="order_id", how="left")
    cols = ["order_id", "order_item_id", "product_id", "seller_id", "customer_unique_id",
            "date_key", "purchase_date", "shipping_limit_date",
            "price", "freight_value", "item_total", "freight_share", "freight_gt_price",
            "order_status", "is_sale",
            # order context (aggregate at order level in DAX!)
            "is_delivered", "is_late", "delivery_days", "review_score", "is_multi_seller"]
    return it[cols].sort_values(["purchase_date", "order_id", "order_item_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Model integrity (fail loudly: a broken model produces wrong KPIs silently)
# --------------------------------------------------------------------------- #
PRIMARY_KEYS = {
    "fact_orders": ["order_id"],
    "fact_order_items": ["order_id", "order_item_id"],
    "dim_customer": ["customer_unique_id"],
    "dim_product": ["product_id"],
    "dim_seller": ["seller_id"],
    "dim_date": ["date_key"],
}
RELATIONSHIPS = [  # (fact, fk, dim, pk) - all many-to-one, single direction
    ("fact_orders", "customer_unique_id", "dim_customer", "customer_unique_id"),
    ("fact_orders", "date_key", "dim_date", "date_key"),
    ("fact_order_items", "customer_unique_id", "dim_customer", "customer_unique_id"),
    ("fact_order_items", "date_key", "dim_date", "date_key"),
    ("fact_order_items", "product_id", "dim_product", "product_id"),
    ("fact_order_items", "seller_id", "dim_seller", "seller_id"),
]


def check_model(model: dict[str, pd.DataFrame], raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Assert the invariants that make the Power BI model correct."""
    rows = []

    def record(check: str, ok: bool, detail: str = "") -> None:
        rows.append({"check": check, "result": "PASS" if ok else "FAIL", "detail": detail})

    for t, pk in PRIMARY_KEYS.items():
        df = model[t]
        record(f"{t}: PK {pk} unique & not null",
               not df.duplicated(pk).any() and not df[pk].isna().any().any(), f"{len(df):,} rows")
    for f, fk, d, pk in RELATIONSHIPS:
        orphans = ~model[f][fk].isin(model[d][pk])
        record(f"{f}.{fk} -> {d}.{pk} (no orphans)", not orphans.any(),
               f"{int(orphans.sum())} orphans")

    record("fact_orders keeps every raw order",
           len(model["fact_orders"]) == len(raw["orders"]), f"{len(raw['orders']):,}")
    record("fact_order_items keeps every raw item",
           len(model["fact_order_items"]) == len(raw["order_items"]), f"{len(raw['order_items']):,}")

    s_items = model["fact_order_items"]["price"].sum()
    s_orders = model["fact_orders"]["items_price"].sum()
    record("Revenue reconciles across facts (sum price)", np.isclose(s_items, s_orders),
           f"{s_items:,.2f} vs {s_orders:,.2f}")
    s_pay_raw = raw["order_payments"]["payment_value"].sum()
    s_pay = model["fact_orders"]["payment_total"].sum()
    record("Payments reconcile with raw table", np.isclose(s_pay_raw, s_pay),
           f"{s_pay:,.2f} vs {s_pay_raw:,.2f}")

    result = pd.DataFrame(rows)
    failed = result[result["result"] == "FAIL"]
    if not failed.empty:
        raise AssertionError("Model integrity failed:\n" + failed.to_string(index=False))
    logger.info("Model integrity: %d/%d checks passed", len(result), len(result))
    return result


def build_model(clean: dict[str, pd.DataFrame], raw: dict[str, pd.DataFrame]
                ) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    fact_orders = build_fact_orders(clean)
    model = {
        "fact_orders": fact_orders,
        "fact_order_items": build_fact_order_items(clean, fact_orders),
        "dim_customer": build_dim_customer(clean, fact_orders),
        "dim_product": build_dim_product(clean),
        "dim_seller": build_dim_seller(clean),
        "dim_date": build_dim_date(raw),
    }
    for name, df in model.items():
        logger.info("Built %-17s %9s rows x %2d cols", name, f"{len(df):,}", df.shape[1])
    return model, check_model(model, raw)
