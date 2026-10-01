"""
Central configuration: paths, table schemas and data-quality rules.

Every rule used by `load.py` and `validate.py` lives here, so the pipeline
stays declarative: to add a check you edit this file, not the logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = ROOT_DIR / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"
LOG_DIR = ROOT_DIR / "logs"

KAGGLE_URL = "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"


# --------------------------------------------------------------------------- #
# Table schemas
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TableSpec:
    """Declarative description of one raw CSV file."""

    name: str                                   # short logical name
    file: str                                   # file name inside data/raw
    dtypes: dict[str, str]                      # non-date columns and pandas dtype
    date_cols: tuple[str, ...] = ()             # parsed as datetime64
    primary_key: tuple[str, ...] = ()           # expected unique key (may be violated)
    zip_cols: tuple[str, ...] = field(default=())  # zip prefixes -> 5-char strings

    @property
    def columns(self) -> list[str]:
        return list(self.dtypes) + list(self.date_cols)


TABLES: dict[str, TableSpec] = {
    "orders": TableSpec(
        name="orders",
        file="olist_orders_dataset.csv",
        dtypes={"order_id": "string", "customer_id": "string", "order_status": "category"},
        date_cols=(
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
        primary_key=("order_id",),
    ),
    "order_items": TableSpec(
        name="order_items",
        file="olist_order_items_dataset.csv",
        dtypes={
            "order_id": "string",
            "order_item_id": "Int16",
            "product_id": "string",
            "seller_id": "string",
            "price": "float64",
            "freight_value": "float64",
        },
        date_cols=("shipping_limit_date",),
        primary_key=("order_id", "order_item_id"),
    ),
    "order_payments": TableSpec(
        name="order_payments",
        file="olist_order_payments_dataset.csv",
        dtypes={
            "order_id": "string",
            "payment_sequential": "Int16",
            "payment_type": "category",
            "payment_installments": "Int16",
            "payment_value": "float64",
        },
        primary_key=("order_id", "payment_sequential"),
    ),
    "order_reviews": TableSpec(
        name="order_reviews",
        file="olist_order_reviews_dataset.csv",
        dtypes={
            "review_id": "string",
            "order_id": "string",
            "review_score": "Int8",
            "review_comment_title": "string",
            "review_comment_message": "string",
        },
        date_cols=("review_creation_date", "review_answer_timestamp"),
        primary_key=("review_id", "order_id"),
    ),
    "customers": TableSpec(
        name="customers",
        file="olist_customers_dataset.csv",
        dtypes={
            "customer_id": "string",
            "customer_unique_id": "string",
            "customer_zip_code_prefix": "string",
            "customer_city": "string",
            "customer_state": "category",
        },
        primary_key=("customer_id",),
        zip_cols=("customer_zip_code_prefix",),
    ),
    "products": TableSpec(
        name="products",
        file="olist_products_dataset.csv",
        dtypes={
            "product_id": "string",
            "product_category_name": "string",
            # NOTE: "lenght" is a typo in the original dataset; renamed in clean.py
            "product_name_lenght": "Int32",
            "product_description_lenght": "Int32",
            "product_photos_qty": "Int16",
            "product_weight_g": "Int32",
            "product_length_cm": "Int32",
            "product_height_cm": "Int32",
            "product_width_cm": "Int32",
        },
        primary_key=("product_id",),
    ),
    "sellers": TableSpec(
        name="sellers",
        file="olist_sellers_dataset.csv",
        dtypes={
            "seller_id": "string",
            "seller_zip_code_prefix": "string",
            "seller_city": "string",
            "seller_state": "category",
        },
        primary_key=("seller_id",),
        zip_cols=("seller_zip_code_prefix",),
    ),
    "geolocation": TableSpec(
        name="geolocation",
        file="olist_geolocation_dataset.csv",
        dtypes={
            "geolocation_zip_code_prefix": "string",
            "geolocation_lat": "float64",
            "geolocation_lng": "float64",
            "geolocation_city": "string",
            "geolocation_state": "category",
        },
        # No natural key: one zip prefix maps to many coordinates by design.
        zip_cols=("geolocation_zip_code_prefix",),
    ),
    "category_translation": TableSpec(
        name="category_translation",
        file="product_category_name_translation.csv",
        dtypes={"product_category_name": "string", "product_category_name_english": "string"},
        primary_key=("product_category_name",),
    ),
}


# --------------------------------------------------------------------------- #
# Referential integrity: (child_table, child_col, parent_table, parent_col)
# --------------------------------------------------------------------------- #
FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("order_items", "seller_id", "sellers", "seller_id"),
    ("order_payments", "order_id", "orders", "order_id"),
    ("order_reviews", "order_id", "orders", "order_id"),
    ("products", "product_category_name", "category_translation", "product_category_name"),
    ("customers", "customer_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix"),
    ("sellers", "seller_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix"),
]

# Reverse coverage: parent rows that SHOULD have at least one child.
# e.g. an order without items cannot generate revenue.
COVERAGE_CHECKS: list[tuple[str, str, str, str]] = [
    ("orders", "order_id", "order_items", "order_id"),
    ("orders", "order_id", "order_payments", "order_id"),
    ("orders", "order_id", "order_reviews", "order_id"),
]


# Nulls that are expected by business logic (reported as INFO, not WARNING).
EXPECTED_NULLS: dict[tuple[str, str], str] = {
    ("order_reviews", "review_comment_title"): "Comments are optional",
    ("order_reviews", "review_comment_message"): "Comments are optional",
    ("orders", "order_approved_at"): "Orders canceled/created before payment approval",
    ("orders", "order_delivered_carrier_date"): "Orders not yet shipped or canceled",
    ("orders", "order_delivered_customer_date"): "Orders not yet delivered or canceled",
}

# Grain assumptions worth verifying beyond the primary key.
# (table, column, why it matters for modelling)
GRAIN_CHECKS: list[tuple[str, str, str]] = [
    ("order_reviews", "review_id",
     "review_id reused across orders -> never use it alone as a key"),
    ("order_reviews", "order_id",
     "orders with >1 review -> aggregate to order level before joining to facts"),
    ("order_payments", "order_id",
     "orders paid with >1 payment row -> aggregate before joining to facts"),
    ("customers", "customer_unique_id",
     "customer_id is per-order; customer_unique_id identifies the person (repeat buyers)"),
]


# --------------------------------------------------------------------------- #
# Domain rules
# --------------------------------------------------------------------------- #
BRAZIL_STATES = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)

ALLOWED_VALUES: dict[tuple[str, str], frozenset] = {
    ("orders", "order_status"): frozenset(
        {"created", "approved", "invoiced", "processing", "shipped",
         "delivered", "canceled", "unavailable"}
    ),
    ("order_payments", "payment_type"): frozenset(
        {"credit_card", "boleto", "voucher", "debit_card"}
    ),
    ("customers", "customer_state"): BRAZIL_STATES,
    ("sellers", "seller_state"): BRAZIL_STATES,
    ("geolocation", "geolocation_state"): BRAZIL_STATES,
}

# (table, column, min, max, inclusive_min) -> values outside are invalid
NUMERIC_RANGES: list[tuple[str, str, float | None, float | None, bool]] = [
    ("order_items", "price", 0, None, False),               # price must be > 0
    ("order_items", "freight_value", 0, None, True),         # free shipping is valid
    ("order_payments", "payment_value", 0, None, True),
    ("order_payments", "payment_installments", 1, 24, True),
    ("order_reviews", "review_score", 1, 5, True),
    ("products", "product_weight_g", 0, None, False),
    ("products", "product_length_cm", 0, None, False),
    ("products", "product_height_cm", 0, None, False),
    ("products", "product_width_cm", 0, None, False),
    ("products", "product_photos_qty", 0, None, True),
]

# Brazil bounding box (incl. oceanic islands) for coordinate sanity checks
BRAZIL_BBOX = {"lat_min": -33.75, "lat_max": 5.27, "lng_min": -73.99, "lng_max": -28.84}


# --------------------------------------------------------------------------- #
# Chronology rules: (table, earlier_col, later_col, description)
# A violation means `later_col < earlier_col` when both are present.
# --------------------------------------------------------------------------- #
DATE_ORDER_RULES: list[tuple[str, str, str, str]] = [
    ("orders", "order_purchase_timestamp", "order_approved_at",
     "Approved before purchase"),
    ("orders", "order_approved_at", "order_delivered_carrier_date",
     "Handed to carrier before approval"),
    ("orders", "order_delivered_carrier_date", "order_delivered_customer_date",
     "Delivered to customer before carrier pickup"),
    ("orders", "order_purchase_timestamp", "order_delivered_customer_date",
     "Delivered before purchase"),
    ("orders", "order_purchase_timestamp", "order_estimated_delivery_date",
     "Estimated delivery before purchase"),
    ("order_reviews", "review_creation_date", "review_answer_timestamp",
     "Review answered before it was created"),
]

# Numeric columns screened for statistical outliers.
# `log=True` -> IQR is computed on log1p(x), appropriate for right-skewed money data.
OUTLIER_COLUMNS: list[tuple[str, str, bool]] = [
    ("order_items", "price", True),
    ("order_items", "freight_value", True),
    ("order_payments", "payment_value", True),
    ("products", "product_weight_g", True),
]
IQR_MULTIPLIER = 1.5

# Seller shipping deadline should fall shortly after purchase. Larger gaps are
# almost certainly data-entry errors (e.g. a deadline set 3 years ahead).
MAX_SHIPPING_LIMIT_DAYS = 60

# Payments vs. items reconciliation: an order's payments should equal
# sum(price + freight_value) of its items. Differences above this (BRL) are flagged.
# Installment interest makes small positive differences legitimate.
PAYMENT_TOLERANCE_BRL = 1.0

# Months with fewer orders than this share of the median month are flagged as
# incomplete (they distort trend and MoM % charts).
INCOMPLETE_MONTH_THRESHOLD = 0.10
