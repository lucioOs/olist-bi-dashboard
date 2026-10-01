"""
Tiny synthetic Olist dataset with KNOWN, deliberate defects.

Each defect is planted once so the tests can assert the validator finds
exactly it. The raw files are written as CSV so `load.py` is tested too.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import TABLES  # noqa: E402
from src.load import load_all  # noqa: E402

TS = "2017-03-01 10:00:00"


def _frames() -> dict[str, pd.DataFrame]:
    orders = pd.DataFrame({
        "order_id": ["o1", "o2", "o3", "o4"],
        "customer_id": ["c1", "c2", "c3", "cX"],            # cX -> orphan customer
        "order_status": ["delivered", "delivered", "canceled", "delivered"],
        "order_purchase_timestamp": [TS, TS, TS, "2017-04-01 10:00:00"],
        "order_approved_at": [TS, TS, None, "2017-04-01 11:00:00"],
        "order_delivered_carrier_date": ["2017-03-02 10:00:00", None, None,
                                         "2017-04-02 10:00:00"],
        # o1: delivered BEFORE purchase (defect); o2: delivered w/o date (defect)
        "order_delivered_customer_date": ["2017-02-20 10:00:00", None, None,
                                          "2017-04-05 10:00:00"],
        "order_estimated_delivery_date": ["2017-03-10 00:00:00"] * 3 + ["2017-04-10 00:00:00"],
    })
    items = pd.DataFrame({
        "order_id": ["o1", "o1", "o2", "o4"],
        "order_item_id": [1, 2, 1, 1],
        "product_id": ["p1", "p1", "p2", "p1"],
        "seller_id": ["s1", "s1", "s1", "s1"],
        "shipping_limit_date": [TS] * 4,
        "price": [100.0, 100.0, 0.0, 50.0],                   # 0.0 -> invalid price
        "freight_value": [10.0, 10.0, 5.0, 60.0],             # 60 > 50 -> freight > price
    })
    payments = pd.DataFrame({
        "order_id": ["o1", "o1", "o2", "o4"],
        "payment_sequential": [1, 2, 1, 1],
        "payment_type": ["credit_card", "voucher", "boleto", "pix"],  # pix -> not allowed
        "payment_installments": [3, 1, 1, 0],                          # 0 -> out of range
        "payment_value": [200.0, 20.0, 5.0, 110.0],
    })
    reviews = pd.DataFrame({
        "review_id": ["r1", "r2", "r2"],                       # r2 reused
        "order_id": ["o1", "o2", "o4"],
        "review_score": [5, 1, 4],
        "review_comment_title": [None, None, "ok"],
        "review_comment_message": ["great", None, None],
        "review_creation_date": [TS] * 3,
        "review_answer_timestamp": ["2017-03-02 10:00:00"] * 3,
    })
    customers = pd.DataFrame({
        "customer_id": ["c1", "c2", "c3"],
        "customer_unique_id": ["u1", "u1", "u2"],              # u1 is a repeat buyer
        "customer_zip_code_prefix": ["1001", "20000", "30000"],  # leading zero lost
        "customer_city": [" sao paulo ", "rio", "bh"],
        "customer_state": ["SP", "RJ", "MG"],
    })
    products = pd.DataFrame({
        "product_id": ["p1", "p2"],
        "product_category_name": ["beleza_saude", "pc_gamer"],   # pc_gamer untranslated
        "product_name_lenght": [40, 30],
        "product_description_lenght": [300, 200],
        "product_photos_qty": [1, 2],
        "product_weight_g": [500, 0],                            # 0 g -> invalid
        "product_length_cm": [20, 10],
        "product_height_cm": [10, 5],
        "product_width_cm": [15, 5],
    })
    sellers = pd.DataFrame({
        "seller_id": ["s1"], "seller_zip_code_prefix": ["01001"],
        "seller_city": ["sao paulo"], "seller_state": ["SP"],
    })
    geo = pd.DataFrame({
        "geolocation_zip_code_prefix": ["01001", "01001", "20000", "20000"],
        "geolocation_lat": [-23.55, -23.55, -22.9, 40.0],       # 40.0 outside Brazil
        "geolocation_lng": [-46.63, -46.63, -43.2, -3.7],
        "geolocation_city": ["sao paulo", "sao paulo", "rio", "madrid"],
        "geolocation_state": ["SP", "SP", "RJ", "RJ"],
    })                                                           # row 2 = exact duplicate
    translation = pd.DataFrame({
        "product_category_name": ["beleza_saude"],
        "product_category_name_english": ["health_beauty"],
    })
    return {"orders": orders, "order_items": items, "order_payments": payments,
            "order_reviews": reviews, "customers": customers, "products": products,
            "sellers": sellers, "geolocation": geo, "category_translation": translation}


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("raw")
    for name, df in _frames().items():
        # translation file written with BOM, as in some Kaggle versions
        enc = "utf-8-sig" if name == "category_translation" else "utf-8"
        df.to_csv(d / TABLES[name].file, index=False, encoding=enc)
    return d


@pytest.fixture(scope="session")
def tables(raw_dir) -> dict[str, pd.DataFrame]:
    return load_all(raw_dir=raw_dir, use_cache=False)
