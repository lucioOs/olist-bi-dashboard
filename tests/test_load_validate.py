"""Unit tests: each planted defect in conftest.py must be detected exactly."""
from __future__ import annotations

import pandas as pd
import pytest

from src import validate as v
from src.load import load_all


def _find(results, check_substr, table=None):
    hits = [r for r in results if check_substr in r.check and (table is None or r.table == table)]
    assert hits, f"No check matching '{check_substr}'"
    return hits[0]


# ---------------------------- load.py ------------------------------------- #
def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Download the dataset"):
        load_all(raw_dir=tmp_path)


def test_types_and_zip_padding(tables):
    assert pd.api.types.is_datetime64_any_dtype(tables["orders"]["order_purchase_timestamp"])
    assert tables["customers"]["customer_zip_code_prefix"].iloc[0] == "01001"
    assert tables["customers"]["customer_city"].iloc[0] == "sao paulo"   # stripped
    assert "product_category_name" in tables["category_translation"].columns  # BOM removed


# ---------------------------- validate.py --------------------------------- #
def test_foreign_keys(tables):
    res = v.check_foreign_keys(tables)
    assert _find(res, "orders.customer_id").n_affected == 1
    assert _find(res, "products.product_category_name").n_affected == 1     # pc_gamer
    assert _find(res, "customers.customer_zip_code_prefix").n_affected == 1  # 30000


def test_grain(tables):
    res = v.check_grain(tables)
    assert _find(res, "review_id").n_affected == 1
    assert _find(res, "customer_unique_id").n_affected == 1


def test_domain_and_ranges(tables):
    assert _find(v.check_allowed_values(tables), "payment_type").n_affected == 1
    rng = v.check_numeric_ranges(tables)
    assert _find(rng, "price").n_affected == 1
    assert _find(rng, "payment_installments").n_affected == 1
    assert _find(rng, "product_weight_g").n_affected == 1


def test_coordinates_and_duplicates(tables):
    assert v.check_coordinates(tables)[0].n_affected == 1
    assert _find(v.check_full_duplicates(tables), "Exact", "geolocation").n_affected == 1


def test_dates_and_status(tables):
    assert _find(v.check_date_order(tables), "Delivered before purchase").n_affected == 1
    assert _find(v.check_status_vs_dates(tables), "without delivery date").n_affected == 1


def test_shipping_limit(tables):
    # o4: purchased 2017-04-01 but shipping deadline 2017-03-01 (before purchase)
    assert v.check_shipping_limit(tables)[0].n_affected == 1


def test_coverage(tables):
    # o3 (canceled) has no items/payments; o3 has no review
    assert _find(v.check_coverage(tables), "without order_items").n_affected == 1


def test_payments_reconciliation(tables):
    # o1: paid 220 = items 220 OK; o2: 5 vs 5 OK; o4: 110 vs 110 OK
    assert v.check_payments_vs_items(tables)[0].n_affected == 0


def test_iqr_bounds_log_scale():
    s = pd.Series([10, 12, 11, 13, 9, 10, 11, 1000])
    lo, hi = v.iqr_bounds(s, log=True)
    assert hi < 1000 < hi * 100 and lo >= 0


def test_run_all_checks_no_crash(tables):
    df = v.run_all_checks(tables)
    assert not (df["dimension"] == "Pipeline").any(), df[df["dimension"] == "Pipeline"]
    assert {"PASS", "CRITICAL", "WARNING", "INFO"} >= set(df["status"])
