-- =============================================================================
-- Olist BI - Business queries
-- Engine: DuckDB (run with `python -m src.run_sql`). Syntax is kept
-- PostgreSQL-compatible: CTEs, window functions, FILTER, PERCENTILE_CONT,
-- CAST AS NUMERIC (no DuckDB-only functions).
-- Tables: the star schema exported to data/processed/ by `python -m src.main`.
--
-- Conventions
--   * Revenue = SUM(price) of sales (is_sale = TRUE). Freight is reported apart.
--   * Trends use complete months only (dim_date.is_complete_month).
--   * Each query starts with "-- name: <id> | <business question>" (parsed by
--     the runner to label the results).
-- =============================================================================


-- name: q01_monthly_trend | How do revenue and orders evolve month over month?
WITH monthly AS (
    SELECT d.year_month,
           COUNT(*)                         AS orders,
           SUM(o.items_price)               AS revenue
    FROM fact_orders o
    JOIN dim_date d ON d.date_key = o.date_key
    WHERE o.is_sale AND o.has_items AND d.is_complete_month
    GROUP BY d.year_month
)
SELECT year_month,
       orders,
       ROUND(CAST(revenue AS NUMERIC), 2)                                   AS revenue,
       ROUND(CAST(revenue / orders AS NUMERIC), 2)                          AS aov,
       ROUND(CAST(100.0 * (revenue / LAG(revenue) OVER w - 1) AS NUMERIC), 2) AS revenue_mom_pct,
       ROUND(CAST(SUM(revenue) OVER (ORDER BY year_month
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS NUMERIC), 2) AS revenue_cumulative,
       ROUND(CAST(AVG(revenue) OVER (ORDER BY year_month
             ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS NUMERIC), 2)      AS revenue_3m_moving_avg
FROM monthly
WINDOW w AS (ORDER BY year_month)
ORDER BY year_month;


-- name: q02_top_categories | Which categories generate the most revenue (Pareto)?
WITH cat AS (
    SELECT p.category,
           SUM(i.price)                  AS revenue,
           COUNT(DISTINCT i.order_id)    AS orders,
           AVG(i.freight_share)          AS avg_freight_share
    FROM fact_order_items i
    JOIN dim_product p ON p.product_id = i.product_id
    WHERE i.is_sale
    GROUP BY p.category
)
SELECT RANK() OVER (ORDER BY revenue DESC)                                 AS rank,
       category,
       orders,
       ROUND(CAST(revenue AS NUMERIC), 2)                                  AS revenue,
       ROUND(CAST(100.0 * revenue / SUM(revenue) OVER () AS NUMERIC), 2)  AS revenue_share_pct,
       ROUND(CAST(100.0 * SUM(revenue) OVER (ORDER BY revenue DESC
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
             / SUM(revenue) OVER () AS NUMERIC), 2)                        AS cumulative_share_pct,
       ROUND(CAST(100.0 * avg_freight_share AS NUMERIC), 1)                AS avg_freight_share_pct
FROM cat
ORDER BY revenue DESC
LIMIT 15;


-- name: q03_revenue_by_state | Which customer states generate the most revenue and the highest ticket?
SELECT c.state,
       c.region,
       COUNT(*)                                                   AS orders,
       COUNT(DISTINCT o.customer_unique_id)                       AS customers,
       ROUND(CAST(SUM(o.items_price) AS NUMERIC), 2)              AS revenue,
       ROUND(CAST(AVG(o.items_price) AS NUMERIC), 2)              AS aov,
       ROUND(CAST(AVG(o.freight_total) AS NUMERIC), 2)            AS avg_freight,
       ROUND(CAST(100.0 * SUM(o.items_price) / SUM(SUM(o.items_price)) OVER () AS NUMERIC), 2)
                                                                  AS revenue_share_pct
FROM fact_orders o
JOIN dim_customer c ON c.customer_unique_id = o.customer_unique_id
WHERE o.is_sale AND o.has_items
GROUP BY c.state, c.region
ORDER BY revenue DESC;


-- name: q04_repeat_customers | What share of customers buy again, and how long until they return?
WITH purchases AS (
    SELECT customer_unique_id,
           order_purchase_timestamp,
           items_price,
           customer_order_seq,
           LEAD(order_purchase_timestamp) OVER (
               PARTITION BY customer_unique_id ORDER BY order_purchase_timestamp
           ) AS next_purchase_ts
    FROM fact_orders
    WHERE is_sale AND has_items
),
per_customer AS (
    SELECT customer_unique_id,
           COUNT(*)                                                    AS orders,
           SUM(items_price)                                            AS revenue,
           MIN(CASE WHEN customer_order_seq = 1
                    THEN CAST(next_purchase_ts AS DATE) - CAST(order_purchase_timestamp AS DATE) END)
                                                                       AS days_to_2nd_purchase
    FROM purchases
    GROUP BY customer_unique_id
)
SELECT COUNT(*)                                                         AS customers,
       COUNT(*) FILTER (WHERE orders >= 2)                              AS repeat_customers,
       ROUND(CAST(100.0 * COUNT(*) FILTER (WHERE orders >= 2) / COUNT(*) AS NUMERIC), 2)
                                                                        AS repeat_customer_pct,
       ROUND(CAST(SUM(revenue) / SUM(orders) AS NUMERIC), 2)            AS aov,
       ROUND(CAST(AVG(revenue) FILTER (WHERE orders = 1) AS NUMERIC), 2) AS avg_revenue_one_time,
       ROUND(CAST(AVG(revenue) FILTER (WHERE orders >= 2) AS NUMERIC), 2) AS avg_revenue_repeat,
       ROUND(CAST(100.0 * SUM(revenue) FILTER (WHERE orders >= 2) / SUM(revenue) AS NUMERIC), 2)
                                                                        AS repeat_revenue_share_pct,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_2nd_purchase)                         AS median_days_to_2nd_purchase
FROM per_customer;


-- name: q05_delivery_by_state | How long do deliveries take and where are they late most often?
WITH by_state AS (
    SELECT c.state,
           COUNT(*)                                         AS delivered_orders,
           AVG(o.delivery_days)                             AS avg_delivery_days,
           AVG(o.promised_days)                             AS avg_promised_days,
           AVG(CASE WHEN o.is_late THEN 1.0 ELSE 0.0 END)   AS late_rate,
           AVG(o.review_score)                              AS avg_review
    FROM fact_orders o
    JOIN dim_customer c ON c.customer_unique_id = o.customer_unique_id
    WHERE o.is_delivered
    GROUP BY c.state
    HAVING COUNT(*) >= 100               -- avoid ranking states on tiny samples
)
SELECT DENSE_RANK() OVER (ORDER BY late_rate DESC)                   AS late_rank,
       state,
       delivered_orders,
       ROUND(CAST(avg_delivery_days AS NUMERIC), 1)                  AS avg_delivery_days,
       ROUND(CAST(avg_promised_days AS NUMERIC), 1)                  AS avg_promised_days,
       ROUND(CAST(100.0 * late_rate AS NUMERIC), 2)                  AS late_pct,
       ROUND(CAST(avg_review AS NUMERIC), 2)                         AS avg_review
FROM by_state
ORDER BY late_rank;


-- name: q06_delay_vs_review | How does delivery delay affect the review score?
SELECT CASE
           WHEN delay_days <= -10 THEN '1. 10+ days early'
           WHEN delay_days <= -1  THEN '2. 1-9 days early'
           WHEN delay_days =   0  THEN '3. On the promised day'
           WHEN delay_days <=  3  THEN '4. 1-3 days late'
           WHEN delay_days <=  7  THEN '5. 4-7 days late'
           ELSE                        '6. 8+ days late'
       END                                                              AS delivery_bucket,
       COUNT(*)                                                         AS orders,
       ROUND(CAST(AVG(review_score) AS NUMERIC), 2)                     AS avg_review,
       ROUND(CAST(100.0 * AVG(CASE WHEN review_score <= 2 THEN 1.0 ELSE 0.0 END) AS NUMERIC), 1)
                                                                        AS negative_review_pct,
       ROUND(CAST(100.0 * AVG(CASE WHEN review_score = 5 THEN 1.0 ELSE 0.0 END) AS NUMERIC), 1)
                                                                        AS five_star_pct
FROM fact_orders
WHERE is_delivered AND has_review
GROUP BY 1
ORDER BY 1;


-- name: q07_payments | Which payment methods dominate, and do installments mean bigger tickets?
WITH buckets AS (
    SELECT main_payment_type,
           CASE WHEN main_payment_type <> 'credit_card' THEN 'n/a (not credit card)'
                WHEN max_installments = 1  THEN '01 installment'
                WHEN max_installments <= 3 THEN '02-03 installments'
                WHEN max_installments <= 6 THEN '04-06 installments'
                ELSE                            '07+ installments'
           END AS installment_bucket,
           items_price
    FROM fact_orders
    WHERE is_sale AND has_items AND main_payment_type IS NOT NULL
)
SELECT main_payment_type,
       installment_bucket,
       COUNT(*)                                                              AS orders,
       ROUND(CAST(100.0 * COUNT(*) / SUM(COUNT(*)) OVER () AS NUMERIC), 2)  AS order_share_pct,
       ROUND(CAST(AVG(items_price) AS NUMERIC), 2)                           AS aov,
       ROUND(CAST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY items_price) AS NUMERIC), 2)            AS median_ticket
FROM buckets
GROUP BY main_payment_type, installment_bucket
ORDER BY main_payment_type, installment_bucket;


-- name: q08_seller_risk | Which sellers combine high volume with poor ratings?
-- Order-level review attributed to the seller; multi-seller orders excluded so
-- each review belongs to exactly one seller.
WITH seller_orders AS (
    SELECT DISTINCT seller_id, order_id, review_score, is_late, is_delivered
    FROM fact_order_items
    WHERE is_sale AND NOT is_multi_seller
),
seller_stats AS (
    SELECT s.seller_id,
           MAX(ds.state)                                                 AS state,
           COUNT(*)                                                      AS orders,
           AVG(s.review_score)                                           AS avg_review,
           AVG(CASE WHEN s.review_score <= 2 THEN 1.0 ELSE 0.0 END)
               FILTER (WHERE s.review_score IS NOT NULL)                 AS negative_rate,
           AVG(CASE WHEN s.is_late THEN 1.0 ELSE 0.0 END)
               FILTER (WHERE s.is_delivered)                             AS late_rate
    FROM seller_orders s
    JOIN dim_seller ds ON ds.seller_id = s.seller_id
    GROUP BY s.seller_id
),
revenue AS (
    SELECT seller_id, SUM(price) AS revenue
    FROM fact_order_items WHERE is_sale GROUP BY seller_id
),
ranked AS (
    SELECT st.*, r.revenue,
           NTILE(4) OVER (ORDER BY st.orders DESC)      AS volume_quartile,
           AVG(st.avg_review) OVER ()                   AS platform_avg_review
    FROM seller_stats st
    JOIN revenue r USING (seller_id)
    WHERE st.orders >= 50                               -- minimum sample for a fair rating
)
SELECT seller_id,
       state,
       orders,
       ROUND(CAST(revenue AS NUMERIC), 2)                      AS revenue,
       ROUND(CAST(avg_review AS NUMERIC), 2)                   AS avg_review,
       ROUND(CAST(100.0 * negative_rate AS NUMERIC), 1)        AS negative_review_pct,
       ROUND(CAST(100.0 * late_rate AS NUMERIC), 1)            AS late_pct,
       volume_quartile
FROM ranked
WHERE volume_quartile <= 2                     -- top half by volume among sellers with 50+ orders
  AND avg_review < platform_avg_review - 0.3   -- clearly below the average of these sellers
ORDER BY orders DESC;


-- name: q09_seller_concentration | How concentrated is revenue among sellers?
WITH s AS (
    SELECT seller_id, SUM(price) AS revenue
    FROM fact_order_items
    WHERE is_sale
    GROUP BY seller_id
),
cum AS (
    SELECT seller_id, revenue,
           ROW_NUMBER() OVER (ORDER BY revenue DESC)                          AS rn,
           COUNT(*) OVER ()                                                   AS n_sellers,
           SUM(revenue) OVER (ORDER BY revenue DESC
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               / SUM(revenue) OVER ()                                         AS cum_share
    FROM s
)
SELECT MAX(n_sellers)                                                         AS sellers,
       MIN(rn) FILTER (WHERE cum_share >= 0.5)                                AS sellers_for_50pct_revenue,
       MIN(rn) FILTER (WHERE cum_share >= 0.8)                                AS sellers_for_80pct_revenue,
       ROUND(CAST(100.0 * MIN(rn) FILTER (WHERE cum_share >= 0.8) / MAX(n_sellers) AS NUMERIC), 2)
                                                                              AS pct_sellers_for_80pct_revenue
FROM cum;
