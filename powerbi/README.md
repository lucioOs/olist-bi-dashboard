# Power BI - Model Setup

Reproducible setup of `olist_dashboard.pbix` from the CSVs exported by `python -m src.main`.

## 1. Load the tables (Power Query)

1. **Transform data > New Source > Blank Query**, then open the **Advanced Editor** and paste
   [`power_query/DataFolder.pq`](power_query/DataFolder.pq). Edit the path so it points to your
   `data\processed\` folder, keeping the trailing backslash. Name the query `DataFolder`.
2. For each table, create another Blank Query, paste its `.pq` file and name the query after the file
   (`fact_orders`, `fact_order_items`, `dim_customer`, `dim_product`, `dim_seller`, `dim_date`).
3. **Close & Apply.**

The scripts declare every column type explicitly with the `en-US` culture. The model loads the same way
on any machine, whatever its regional settings. Money columns use Fixed Decimal (`Currency.Type`).

## 2. Relationships (Model view)

Delete any relationships Power BI auto-detected, then create these six:

| From (many) | To (one) | Cardinality | Cross-filter |
|---|---|---|---|
| `fact_orders[customer_unique_id]` | `dim_customer[customer_unique_id]` | Many-to-one | Single |
| `fact_orders[date_key]` | `dim_date[date_key]` | Many-to-one | Single |
| `fact_order_items[customer_unique_id]` | `dim_customer[customer_unique_id]` | Many-to-one | Single |
| `fact_order_items[date_key]` | `dim_date[date_key]` | Many-to-one | Single |
| `fact_order_items[product_id]` | `dim_product[product_id]` | Many-to-one | Single |
| `fact_order_items[seller_id]` | `dim_seller[seller_id]` | Many-to-one | Single |

The two fact tables are **not** related to each other. They share `dim_customer` and `dim_date`.

## 3. Model settings

- **Mark as date table:** `dim_date`, using the `date` column.
- **Sort by column:**
  - `dim_date[year_month]` by `year_month_num`
  - `dim_date[month_name]` and `month_short` by `month_num`
  - `dim_date[day_name]` by `day_of_week_num`
- **Hide foreign keys** on the fact tables (`date_key`, `customer_unique_id`, `product_id`, `seller_id`).
  Slicers must use the dimension columns.
- **Data categories:** `lat` = Latitude, `lng` = Longitude, `state` = State or Province, `city` = City.
- **Turn off Auto date/time:** File > Options > Current file > Data load.

## 4. Measures

Create a table `_Measures` (Home > Enter data > OK). Add every measure from
[`measures.dax`](measures.dax) into it, grouped in display folders 1-6.

## 5. Reconciliation (Power BI vs. Python)

With no filters applied, the measures must return these values. They were computed independently
with pandas from the same CSVs (`Seller Risk Flag` also matches SQL query `q08`).
If a value differs, check relationships and column types before building visuals.

| Measure | Expected |
|---|---|
| Revenue | R$ 13,494,400.74 |
| Orders | 98,199 |
| AOV | R$ 137.42 |
| Items Sold | 112,101 |
| Customers | 94,983 |
| Repeat Customers | 2,887 |
| Repeat Customer % | 3.04% |
| Delivered Orders | 96,470 |
| Late Orders | 6,534 |
| On-Time Delivery % | 93.23% |
| Avg Delivery Days | 12.56 |
| Avg Promised Days | 23.74 |
| Avg Review Score | 4.086 |
| Negative Review % | 14.69% |
| Avg Review On Time | 4.29 |
| Avg Review Late | 2.27 |
| Revenue MoM % (year_month = 2018-08) | -3.32% |
| Installment Orders % | 51.51% |
| Sellers with `Seller Risk Flag` = 1 (table on `dim_seller[seller_id]`) | 43 |
