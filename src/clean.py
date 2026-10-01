"""
Cleaning layer: apply every treatment recommended in the data-quality report.

Principles
----------
* **Never delete business events silently.** Rows are dropped only when they are
  exact duplicates or physically impossible (coordinates outside Brazil).
  Everything else is *flagged* so the analyst decides per metric.
* **Every action is logged** with the number of rows it touched. The log is
  exported to reports/cleaning_log.md, so each number in the dashboard can be
  traced back to a documented decision.
* Raw DataFrames are never mutated in place (functions work on copies).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src import config as cfg

logger = logging.getLogger(__name__)

# Two categories missing from the official translation file (found by validate.py)
MANUAL_TRANSLATIONS = {
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "portable_kitchen_food_processors",
}
UNKNOWN = "unknown"

# Order statuses that never became a sale -> excluded from revenue KPIs
NON_SALE_STATUSES = {"canceled", "unavailable"}


# --------------------------------------------------------------------------- #
# Cleaning log
# --------------------------------------------------------------------------- #
@dataclass
class CleaningLog:
    entries: list[dict] = field(default_factory=list)

    def add(self, table: str, action: str, n_rows: int, rationale: str) -> None:
        self.entries.append(
            {"table": table, "action": action, "rows_affected": int(n_rows), "rationale": rationale}
        )
        logger.info("%-15s %-55s %8s rows", table, action, f"{int(n_rows):,}")

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries)


# --------------------------------------------------------------------------- #
# Geolocation -> one coordinate per zip prefix
# --------------------------------------------------------------------------- #
def clean_geolocation(geo: pd.DataFrame, log: CleaningLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns
    -------
    zip_coords  : one row per zip prefix (median lat/lng)
    city_coords : one row per (city, state), used as fallback for unknown zips
    """
    g = geo.copy()
    n0 = len(g)
    g = g.drop_duplicates()
    log.add("geolocation", "Drop exact duplicate rows", n0 - len(g),
            "Repeated GPS samples add no information")

    b = cfg.BRAZIL_BBOX
    inside = (g["geolocation_lat"].between(b["lat_min"], b["lat_max"])
              & g["geolocation_lng"].between(b["lng_min"], b["lng_max"]))
    log.add("geolocation", "Drop coordinates outside Brazil", (~inside).sum(),
            "Physically impossible for a Brazilian zip code")
    g = g[inside]

    # Median is robust to the remaining noisy samples within a zip prefix
    zip_coords = (g.groupby("geolocation_zip_code_prefix", as_index=False)
                   .agg(lat=("geolocation_lat", "median"), lng=("geolocation_lng", "median"))
                   .rename(columns={"geolocation_zip_code_prefix": "zip_code_prefix"}))
    log.add("geolocation", "Aggregate to 1 row per zip prefix (median lat/lng)",
            len(g) - len(zip_coords),
            "Avoids a many-to-many join; one coordinate per zip")

    g["city_norm"] = normalize_city(g["geolocation_city"])
    city_coords = (g.groupby(["city_norm", "geolocation_state"], as_index=False, observed=True)
                    .agg(lat=("geolocation_lat", "median"), lng=("geolocation_lng", "median"))
                    .rename(columns={"geolocation_state": "state"}))
    return zip_coords, city_coords


def normalize_city(s: pd.Series) -> pd.Series:
    """Lower-case, strip accents and extra spaces ('São Paulo ' -> 'sao paulo')."""
    return (s.astype("string").str.lower().str.strip()
             .str.normalize("NFKD").str.encode("ascii", "ignore").str.decode("ascii")
             .str.replace(r"\s+", " ", regex=True))


def attach_coordinates(df: pd.DataFrame, zip_col: str, city_col: str, state_col: str,
                       zip_coords: pd.DataFrame, city_coords: pd.DataFrame,
                       table: str, log: CleaningLog) -> pd.DataFrame:
    """Add lat/lng by zip prefix; fall back to the city centroid when the zip is unknown."""
    out = df.merge(zip_coords, left_on=zip_col, right_on="zip_code_prefix", how="left") \
            .drop(columns="zip_code_prefix")
    missing = out["lat"].isna()
    out["geo_source"] = np.where(missing, "city_centroid", "zip")

    fb = (out.loc[missing, [city_col, state_col]]
             .assign(city_norm=lambda d: normalize_city(d[city_col]),
                     state=lambda d: d[state_col].astype(str))
             .merge(city_coords.assign(state=lambda d: d["state"].astype(str)),
                    on=["city_norm", "state"], how="left"))
    out.loc[missing, "lat"] = fb["lat"].to_numpy()
    out.loc[missing, "lng"] = fb["lng"].to_numpy()
    out[["lat", "lng"]] = out[["lat", "lng"]].round(6)   # ~0.1 m precision is plenty
    still = out["lat"].isna()
    out.loc[still, "geo_source"] = "missing"
    log.add(table, "Zip without coordinates -> city centroid fallback",
            int(missing.sum() - still.sum()), "Keeps the row on the map at city precision")
    log.add(table, "No coordinates available (left NULL)", int(still.sum()),
            "Excluded from maps only")
    return out


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
def clean_products(products: pd.DataFrame, translation: pd.DataFrame,
                   log: CleaningLog) -> pd.DataFrame:
    p = products.rename(columns={
        "product_name_lenght": "product_name_length",            # fix source typo
        "product_description_lenght": "product_description_length",
    }).copy()

    n_null = p["product_category_name"].isna().sum()
    p["product_category_name"] = p["product_category_name"].fillna(UNKNOWN)
    log.add("products", "Missing category -> 'unknown'", n_null,
            "Keeps 610 products (and their sales) in category totals")

    tr = dict(zip(translation["product_category_name"],
                  translation["product_category_name_english"]))
    n_manual = p["product_category_name"].isin(MANUAL_TRANSLATIONS).sum()
    tr.update(MANUAL_TRANSLATIONS)
    tr[UNKNOWN] = UNKNOWN
    p["category"] = p["product_category_name"].map(tr).fillna(UNKNOWN)
    log.add("products", "Manual translation of 2 untranslated categories", n_manual,
            "Missing from the official translation table")
    # Human-readable label for the dashboard: 'health_beauty' -> 'Health Beauty'
    p["category"] = p["category"].str.replace("_", " ").str.title()

    zero_w = (p["product_weight_g"] == 0).fillna(False)
    p.loc[zero_w, "product_weight_g"] = pd.NA
    log.add("products", "Weight = 0 g -> NULL", zero_w.sum(),
            "A shipped product cannot weigh 0 g")

    p["product_volume_cm3"] = (p["product_length_cm"] * p["product_height_cm"]
                               * p["product_width_cm"]).astype("Float64")
    return p


# --------------------------------------------------------------------------- #
# Payments -> one row per order
# --------------------------------------------------------------------------- #
def clean_payments(payments: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    pay = payments.copy()

    zero_inst = pay["payment_installments"] == 0
    pay.loc[zero_inst, "payment_installments"] = 1
    log.add("order_payments", "Installments = 0 -> 1", zero_inst.sum(),
            "A payment is made in at least one installment")

    pay["payment_type"] = pay["payment_type"].astype(str)
    log.add("order_payments", "Keep 'not_defined' payment type", (pay["payment_type"] == "not_defined").sum(),
            "All 3 rows belong to canceled orders with value 0")

    # Main method = the one carrying the largest share of the order value
    main = (pay.sort_values(["order_id", "payment_value"], ascending=[True, False])
               .drop_duplicates("order_id")[["order_id", "payment_type"]]
               .rename(columns={"payment_type": "main_payment_type"}))
    agg = (pay.groupby("order_id", as_index=False)
              .agg(payment_total=("payment_value", "sum"),
                   payment_rows=("payment_sequential", "count"),
                   payment_methods=("payment_type", "nunique"),
                   max_installments=("payment_installments", "max"),
                   used_voucher=("payment_type", lambda s: (s == "voucher").any())))
    out = agg.merge(main, on="order_id", how="left")
    log.add("order_payments", "Aggregate to 1 row per order", len(pay) - len(out),
            "Prevents double counting when joined to orders")
    return out


# --------------------------------------------------------------------------- #
# Reviews -> one row per order
# --------------------------------------------------------------------------- #
def clean_reviews(reviews: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    r = reviews.drop_duplicates().copy()
    r["has_comment"] = r["review_comment_message"].notna() | r["review_comment_title"].notna()

    # Several reviews for one order (202 with different scores): keep the most
    # recent one -> the customer's final opinion. Count is kept for transparency.
    r = r.sort_values(["order_id", "review_creation_date", "review_answer_timestamp"])
    counts = r.groupby("order_id").size().rename("review_count")
    latest = r.drop_duplicates("order_id", keep="last")
    out = (latest[["order_id", "review_score", "has_comment",
                   "review_creation_date", "review_answer_timestamp"]]
           .merge(counts, on="order_id"))
    out["review_response_hours"] = (
        (out["review_answer_timestamp"] - out["review_creation_date"]).dt.total_seconds() / 3600
    ).round(1)
    log.add("order_reviews", "Keep latest review per order", len(r) - len(out),
            "Final customer opinion; avoids double counting in Avg Review Score")
    return out


# --------------------------------------------------------------------------- #
# Orders (delivery features + data-quality flags)
# --------------------------------------------------------------------------- #
def clean_orders(orders: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    o = orders.copy()
    o["order_status"] = o["order_status"].astype(str)
    purchase = o["order_purchase_timestamp"]
    approved = o["order_approved_at"]
    carrier = o["order_delivered_carrier_date"]
    delivered = o["order_delivered_customer_date"]
    estimated = o["order_estimated_delivery_date"]

    # --- flags from validate.py findings -----------------------------------
    o["flag_carrier_before_approval"] = (carrier < approved).fillna(False)
    o["flag_delivered_before_carrier"] = (delivered < carrier).fillna(False)
    o["is_sale"] = ~o["order_status"].isin(NON_SALE_STATUSES)

    # A delivery is usable for logistics KPIs only if status AND dates agree
    o["is_delivered"] = (
        (o["order_status"] == "delivered") & delivered.notna() & (delivered >= purchase)
    )
    log.add("orders", "Delivered status without delivery date -> not delivered",
            ((o["order_status"] == "delivered") & delivered.isna()).sum(),
            "Cannot measure delivery time without a date")
    log.add("orders", "Flag: handed to carrier before approval",
            o["flag_carrier_before_approval"].sum(),
            "Excluded from approval->carrier lead time only")
    log.add("orders", "Flag: delivered before carrier pickup",
            o["flag_delivered_before_carrier"].sum(),
            "Excluded from carrier->customer lead time only")

    # --- delivery metrics (days, fractional) --------------------------------
    days = lambda a, b: (b - a).dt.total_seconds() / 86_400  # noqa: E731
    d = o["is_delivered"]
    o["delivery_days"] = days(purchase, delivered).where(d).round(2)
    o["approval_hours"] = (days(purchase, approved) * 24).where(approved >= purchase).round(2)
    o["carrier_lead_days"] = days(approved, carrier).where(~o["flag_carrier_before_approval"]).round(2)
    o["last_mile_days"] = days(carrier, delivered).where(d & ~o["flag_delivered_before_carrier"]).round(2)
    o["promised_days"] = days(purchase, estimated).round(2)

    # The estimate has no time component (always 00:00) -> compare calendar dates
    o["delay_days"] = (delivered.dt.normalize() - estimated.dt.normalize()).dt.days.where(d)
    o["is_late"] = (o["delay_days"] > 0).where(d).astype("boolean")

    o["purchase_date"] = purchase.dt.normalize()
    return o


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #
def clean_items(items: pd.DataFrame, orders: pd.DataFrame, log: CleaningLog) -> pd.DataFrame:
    it = items.merge(orders[["order_id", "order_purchase_timestamp"]], on="order_id", how="left")
    gap = (it["shipping_limit_date"] - it["order_purchase_timestamp"]).dt.days
    bad = (gap < 0) | (gap > cfg.MAX_SHIPPING_LIMIT_DAYS)
    it.loc[bad, "shipping_limit_date"] = pd.NaT
    log.add("order_items", "Implausible shipping_limit_date -> NULL", bad.sum(),
            f"Deadline outside 0-{cfg.MAX_SHIPPING_LIMIT_DAYS} days after purchase")

    it["item_total"] = it["price"] + it["freight_value"]
    it["freight_gt_price"] = it["freight_value"] > it["price"]
    it["freight_share"] = (it["freight_value"] / it["item_total"]).round(4)
    log.add("order_items", "Flag: freight greater than price", it["freight_gt_price"].sum(),
            "Kept; used in logistics-cost analysis")
    return it.drop(columns="order_purchase_timestamp")


# --------------------------------------------------------------------------- #
# Customers & sellers
# --------------------------------------------------------------------------- #
def clean_customers(customers: pd.DataFrame, orders: pd.DataFrame,
                    zip_coords, city_coords, log: CleaningLog) -> pd.DataFrame:
    """
    One row per PERSON (customer_unique_id). When a person used several
    addresses, the address of their most recent order is kept.
    """
    c = customers.merge(orders[["customer_id", "order_purchase_timestamp"]],
                        on="customer_id", how="left")
    c["customer_city"] = normalize_city(c["customer_city"])
    latest = (c.sort_values("order_purchase_timestamp")
               .drop_duplicates("customer_unique_id", keep="last")
               .drop(columns=["customer_id", "order_purchase_timestamp"]))
    log.add("customers", "Collapse customer_id -> customer_unique_id", len(c) - len(latest),
            "customer_id is per order; the person is customer_unique_id")
    return attach_coordinates(latest, "customer_zip_code_prefix", "customer_city",
                              "customer_state", zip_coords, city_coords, "customers", log)


def clean_sellers(sellers: pd.DataFrame, zip_coords, city_coords,
                  log: CleaningLog) -> pd.DataFrame:
    s = sellers.copy()
    s["seller_city"] = normalize_city(s["seller_city"])
    return attach_coordinates(s, "seller_zip_code_prefix", "seller_city", "seller_state",
                              zip_coords, city_coords, "sellers", log)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def clean_all(tables: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], CleaningLog]:
    log = CleaningLog()
    zip_coords, city_coords = clean_geolocation(tables["geolocation"], log)
    orders = clean_orders(tables["orders"], log)
    clean = {
        "orders": orders,
        "order_items": clean_items(tables["order_items"], tables["orders"], log),
        "payments": clean_payments(tables["order_payments"], log),
        "reviews": clean_reviews(tables["order_reviews"], log),
        "products": clean_products(tables["products"], tables["category_translation"], log),
        "customers": clean_customers(tables["customers"], tables["orders"],
                                     zip_coords, city_coords, log),
        "customer_map": tables["customers"][["customer_id", "customer_unique_id"]],
        "sellers": clean_sellers(tables["sellers"], zip_coords, city_coords, log),
    }
    return clean, log
