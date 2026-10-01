# Dashboard Specification

Design contract for the 4-page report. **Every visual answers a named business question.**
If a visual cannot be mapped to a question in this file, it does not go in the report.

## Global design system

| Element | Rule |
|---|---|
| Canvas | 16:9, 1280 x 720 px. View > Snap to grid on |
| Grid | 16 px outer margin, 16 px gutter between visuals |
| Theme | [`theme/olist_theme.json`](theme/olist_theme.json): page `#F9F9F7`, cards `#FCFCFB`, 1 px hairline border `#E1E0D9`, radius 8, no shadows |
| Typography | Segoe UI. Visual titles 12 pt semibold, labels 9-10 pt, KPI values 24 pt |
| Color roles | Blue `#2A78D6` = default series. Status colors only for good/bad: green `#0CA30C`, red `#D03B3B`. Gray `#C3C2B7` = context / not highlighted |
| Charts | No dual axes, no 3D, no pie/donut with more than 2 slices, no gradients. Sort bars by value |
| Labels | Direct data labels on bars with 10 or fewer items. Titles state the question or the insight, not the field name |
| Time | Trend visuals filter `dim_date[is_complete_month] = True` (visual-level filter) |

### Layout bands (y coordinates)

```
  0 -  56  Header: page title + subtitle (text box) + data note ([Last Refresh Label])
 64 - 112  Slicer row: Date range | Region | State | Category   (synced on all pages)
120 - 216  KPI row: 4-6 cards
232 - 704  Analysis area: 2-4 visuals
```

### Slicers (Sync slicers: all 4 pages)

| Slicer | Field | Style | Default |
|---|---|---|---|
| Purchase date | `dim_date[date]` | Between | 2017-01-01 to 2018-08-31 |
| Region | `dim_customer[region]` | Dropdown, multi-select | All |
| State | `dim_customer[state]` | Dropdown, multi-select | All |
| Category | `dim_product[category]` | Dropdown, search on | All |

The category slicer filters only item-grain measures (Revenue, Orders, AOV). Order-grain KPIs (delivery,
reviews) are not product-specific; this is by design and is noted in the page subtitle.

---

## Page 1 - Executive Summary

**Audience:** leadership. **Question:** *Is the business growing, and where is it losing customers?*

| # | Visual | Fields / measures | Answers |
|---|---|---|---|
| 1.1 | 6 KPI cards (new Card) | Revenue, Orders, AOV, On-Time Delivery %, Avg Review Score, Repeat Customer % | Headline health. Reference label: Revenue MoM % |
| 1.2 | Line chart | X `dim_date[year_month]`, Y Revenue | Q1 How does revenue evolve? |
| 1.3 | Bar chart (horizontal) | Y `dim_product[category]` (Top 10 by Revenue), X Revenue | Q1 Which categories drive revenue? |
| 1.4 | Column chart | X `fact_orders[delivery_bucket]`, Y Avg Review Score | Q4 The key insight: lateness destroys satisfaction |
| 1.5 | Text box (insight) | Static text written from the numbers | "Late orders score 2.27 vs 4.29 on time; only 3% of customers return" |

```
+--------------------------------------------------------------------------------------+
| Executive Summary                                                    data note       |
| [Date range........] [Region v] [State v] [Category v]                               |
| [Revenue] [Orders] [AOV] [On-Time %] [Avg Review] [Repeat %]                         |
| +-------------------------------------------+ +------------------------------------+ |
| | 1.2 Revenue by month (line)               | | 1.3 Top 10 categories by revenue   | |
| +-------------------------------------------+ +------------------------------------+ |
| +-------------------------------------------+ +------------------------------------+ |
| | 1.4 Avg review by delivery timing         | | 1.5 Key insights (text)            | |
| +-------------------------------------------+ +------------------------------------+ |
+--------------------------------------------------------------------------------------+
```

## Page 2 - Sales & Customers

**Questions:** Q1 (sales), Q2 (customers), Q5 (payments).

| # | Visual | Fields / measures | Answers |
|---|---|---|---|
| 2.1 | 4 KPI cards | Revenue, AOV, Customers, Revenue per Customer | |
| 2.2 | Column chart | X `year_month`, Y Revenue MoM %; conditional color: green if > 0, red if < 0 | Q1 Month-over-month growth |
| 2.3 | Bar chart | Y `dim_customer[state]`, X Revenue, sorted desc; tooltip: AOV, Orders | Q1 Which states generate most revenue? |
| 2.4 | Matrix | Rows `dim_product[category]`; values Revenue, Orders, AOV, Freight % of Revenue; data bars on Revenue | Q1 Category detail |
| 2.5 | Column chart | X `fact_orders[installment_bucket]`, Y AOV by Order | Q5 Do installments mean bigger tickets? |
| 2.6 | Bar chart (100%) | Y `fact_orders[main_payment_type]`, X Payment Method Share % | Q5 Which payment methods dominate? |
| 2.7 | KPI cards | Repeat Customers, Repeat Customer %, Repeat Purchase % | Q2 Do customers come back? |

## Page 3 - Operations & Delivery

**Question:** Q3 *How long does delivery take, how often is it late, and where is it worst?*

| # | Visual | Fields / measures | Answers |
|---|---|---|---|
| 3.1 | 5 KPI cards | Avg Delivery Days, Avg Promised Days, Promise Buffer Days, On-Time Delivery %, Late Orders | |
| 3.2 | Line chart (2 series, same unit) | X `year_month`, Y Avg Delivery Days and Avg Promised Days | Is the promise realistic? |
| 3.3 | Bar chart | Y `dim_customer[state]`, X Late Delivery %, sorted desc; red if above the national average | Q3 Where is lateness worst? |
| 3.4 | Stacked bar | Y `dim_customer[region]`, X Avg Carrier Lead Days + Avg Last Mile Days | Seller handling vs. carrier: who is slow? |
| 3.5 | Column chart | X `delivery_bucket`, Y Delivered Orders | Distribution of delivery vs. promise |

## Page 4 - Satisfaction & Sellers

**Questions:** Q4 (satisfaction), Q6 (sellers).

| # | Visual | Fields / measures | Answers |
|---|---|---|---|
| 4.1 | 4 KPI cards | Avg Review Score, Negative Review %, 5-Star Review %, Review Gap Late vs On Time | |
| 4.2 | Column chart | X `delivery_bucket`, Y Avg Review Score; conditional color by value | Q4 Effect of delay on rating |
| 4.3 | Column chart | X `delivery_bucket`, Y Negative Review % | Q4 Same story as % of 1-2 star reviews (separate chart, no dual axis) |
| 4.4 | Scatter chart | Details `dim_seller[seller_label]`; X Seller Orders, Y Seller Avg Review, size Revenue; visual filter Seller Orders >= 50; Y constant line at 3.83 (platform avg - 0.3) | Q6 Volume vs. rating |
| 4.5 | Table | `seller_label`, `state`, Seller Orders, Revenue, Seller Avg Review, Seller Negative Review %, Seller Late %; visual filter Seller Risk Flag = 1; red background scale on Negative % | Q6 Which sellers to act on (43) |

---

## Interaction rules

- Cross-filtering on (default). Edit interactions so KPI cards are **filtered** by charts and the text box is static.
- Tooltips: add AOV and Orders to every bar of revenue; add Late Delivery % to state bars.
- Page navigation: 4 buttons in the header (or Power BI page navigator), same position on every page.
- Accessibility: alt text on every visual (Format > General > Alt text) describing the question it answers.

## Export checklist (for the README)

- [ ] Screenshot of each page at 1280 x 720 into `images/` (`01_executive.png` ... `04_satisfaction.png`)
- [ ] Model view screenshot `images/05_model.png`
- [ ] Hide the QA page before publishing
