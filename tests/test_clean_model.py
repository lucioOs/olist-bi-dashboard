"""Tests for the cleaning layer and the star-schema builder (synthetic data in conftest.py)."""
from __future__ import annotations

import pandas as pd
import pytest

from src import clean as c
from src.model import build_dim_date, build_model


@pytest.fixture(scope="module")
def log():
    return c.CleaningLog()


def test_payments_aggregated_to_order(tables, log):
    pay = c.clean_payments(tables["order_payments"], log)
    assert pay["order_id"].is_unique
    o1 = pay.set_index("order_id").loc["o1"]
    assert o1["payment_total"] == 220 and o1["payment_methods"] == 2
    assert o1["main_payment_type"] == "credit_card" and bool(o1["used_voucher"])
    assert pay["max_installments"].min() >= 1                 # 0 installments fixed


def test_reviews_one_per_order(tables, log):
    rev = c.clean_reviews(tables["order_reviews"], log)
    assert rev["order_id"].is_unique


def test_products_translation_and_unknown(tables, log):
    p = c.clean_products(tables["products"], tables["category_translation"], log)
    cats = dict(zip(p["product_id"], p["category"]))
    assert cats["p1"] == "Health Beauty"
    assert cats["p2"] == "Pc Gamer"                           # manual translation
    assert pd.isna(p.loc[p["product_id"] == "p2", "product_weight_g"]).all()  # 0 g -> NULL
    assert "product_name_length" in p.columns                 # typo fixed


def test_orders_delivery_logic(tables, log):
    o = c.clean_orders(tables["orders"], log).set_index("order_id")
    assert not o.loc["o1", "is_delivered"]                    # delivered before purchase
    assert not o.loc["o2", "is_delivered"]                    # status delivered, no date
    assert not o.loc["o3", "is_sale"]                         # canceled
    assert o.loc["o4", "is_delivered"]
    assert o.loc["o4", "delivery_days"] == pytest.approx(4.0)       # 04-01 10:00 -> 04-05 10:00
    assert o.loc["o4", "delay_days"] == -5 and not o.loc["o4", "is_late"]


def test_geolocation_one_row_per_zip(tables, log):
    zips, _ = c.clean_geolocation(tables["geolocation"], log)
    assert zips["zip_code_prefix"].is_unique
    assert zips["lat"].between(-34, 6).all()                  # point outside Brazil removed


def test_normalize_city():
    s = pd.Series(["  São   Paulo ", "BRASÍLIA"])
    assert c.normalize_city(s).tolist() == ["sao paulo", "brasilia"]


def test_dim_date_full_years(tables):
    d = build_dim_date(tables)
    assert d["date"].min() == pd.Timestamp("2017-01-01")
    assert d["date"].max() == pd.Timestamp("2017-12-31")
    assert d["date_key"].is_unique


def test_model_fails_loudly_on_orphans(tables):
    # conftest plants order o4 with a customer_id missing from customers:
    # the model must refuse to export instead of silently dropping it.
    cleaned, _ = c.clean_all(tables)
    with pytest.raises(AssertionError, match="orphans"):
        build_model(cleaned, tables)
