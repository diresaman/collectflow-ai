# Analytics, statements and forecast: how to read the figures

The analytics are an **assignment demonstration** of deterministic accounts-receivable reporting (M14). The
worker computes each figure from CollectFlow's own records; there is no AI and no external data. The
parameters below are still **proposals awaiting a final owner review**, and this release does not change them.
The formulas are in `docs/v2/10-m14-implementation-report.md` §3, and the code is in
`backend/apps/insights/analytics.py`.

## 1. What the page shows (`/analytics`)

| Figure | Definition |
| --- | --- |
| Summary strip | Overdue amount, expected cash in the next 30 days, DSO and open promises (count and amount) |
| Aging | Unpaid invoices that are still in the sheet, by days past due: current, 1–30, 31–60, 61–90, 90+. Count and total |
| DSO *(proposed)* | Closing receivables ÷ amount billed in the last 30 days × 30 |
| CEI *(proposed, standard formula)* | (opening AR + billed − closing AR) ÷ (opening AR + billed − closing current AR) × 100 |
| Payments after reminders *(proposed)* | Each payment seen in the last 90 days is linked to the latest reminder or statement sent before it, when that was within 7 days |
| 30-day cash forecast *(proposed)* | The expected amount of each unpaid invoice, by week (section 4) |

Every figure is calculated **per currency**. Amounts in different currencies are never added, compared or
converted. A portfolio in USD and EUR shows two aging tables, two DSO values and two forecasts.

## 2. Where the dates come from

The sheet has a due date and a status, but no invoice date and no payment date. So:

- **"Billed"** means the moment CollectFlow first saw the invoice: the first sync that imported it. An invoice
  created weeks before the first import counts as billed on the import day.
- **"Paid"** means the **first sync that showed the row as `PAID`**. This is the date CollectFlow observed the
  status change, **not the date the bank settled the payment**. The gap covers:
  - the time until someone updates the sheet;
  - the time until the next sync, which runs on a schedule;
  - weekends and bank processing.

  "Days late" and the payment timing can therefore read later than reality.
- Invoices that were **already `PAID` when imported** have no observed payment date. They are left out of the
  lateness and timing figures and counted as unmeasured.
- Invoices **removed from the sheet** are left out of every figure. They are not treated as collected or as
  written off.

## 3. DSO and CEI need history

DSO and CEI depend on the receivables at the **start** of the 30-day period. CollectFlow only knows an opening
balance for invoices it saw before that day, and **opening balances are never estimated**. Until CollectFlow
has watched the sheet for a full period, the page shows **"insufficient history"** with the date the figure
becomes available, rather than a number. On the current deployment that is **about 2026-11-15**.

After that, two limits still apply:
- the paid date is the date CollectFlow observed (section 2), so DSO and CEI inherit the lag;
- a one-time bulk import inflates "billed" in the period when it happened.

If a denominator is zero, the page says the figure is undefined and why, instead of showing 0 or 100 %.

## 4. Forecast assumptions

For each unpaid invoice that is still in the sheet, the expected amount is the invoice amount × P. The amount
is placed in the week of its expected date.

| Situation | P | Expected date |
| --- | --- | --- |
| An open dispute | 0. Listed as excluded | — |
| An open promise dated within 30 days | The client's kept-promise rate if it has at least 3 settled promises; otherwise the portfolio rate; otherwise 0.5. The last two are marked low confidence | The promised date |
| Otherwise, by aging bucket | current 0.80 · 1–30 0.60 · 31–60 0.40 · 61–90 0.25 · 90+ 0.10 | Due date + average days late (the client's, if it has at least 3 measured payments; otherwise the portfolio's; otherwise 0, marked low confidence) |
| The expected date falls after the 30-day window | P × 0.25 | — |
| A promise broken in the last 180 days | P × 0.5 | — |
| An unconfirmed "we paid" claim | No change. The invoice is flagged | — |

**Known effect:** an overdue invoice whose expected date has already passed is dated today, so the forecast
places much of the overdue balance in week 1. M15 prepared a correction, `FORECAST_OVERDUE_LAG_DAYS`. It moves
such invoices 7, 14, 21 or 28 days ahead by aging bucket, and the row says so. It is **off by default** until
the owner approves it.

## 5. Why the forecast is not a promise of cash

- The probabilities are **fixed heuristics** chosen for the demonstration. They have **not been fitted** to this
  business's payment history, which is too short to fit anything.
- Expected value is a probability-weighted amount. An invoice of 1,000 at P = 0.6 adds 600, but the customer
  will pay either 1,000 or nothing. **No single week's total is an amount anyone has committed to pay.**
- Dates use averages: one late payment moves a client's average.
- Promises and disputes change as replies arrive. The figures refresh hourly and after relevant changes, so the
  forecast can move between visits.
- Payment dates are observation dates (section 2), so actual cash may have arrived earlier.

The page labels the forecast **"estimate"**. It must not be used as guaranteed collections, as a basis for
lending, or as an accounting figure. It is a guide to where the collection effort matters most.

## 6. Payments after reminders

This links a payment to the latest reminder sent within 7 days before it. **It is a time association, not
proof that the reminder caused the payment**, and the page says so. With the observed paid date the window is
approximate.

## 7. Consolidated statements

A statement combines a client's chaseable overdue invoices (at least 2) into one email:
- Gemini writes only the subject, opening and closing; it never sees amounts;
- the backend writes the invoice lines and the totals per currency;
- an approver who did not edit the statement must approve it;
- every listed invoice is checked again at approval, send reservation and delivery. If any invoice changed in
  the sheet, the statement is refused as stale.
