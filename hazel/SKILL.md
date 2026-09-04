---
name: household-pricing
description: >
  Hazel's household procurement system. Price comparison across King Soopers
  (Kroger API), Walmart and Amazon (SerpApi); need-slot catalog; product
  verdicts; price history; shrinkflation and price-change alerting; weekly
  basket optimization under free-shipping thresholds; pantry state with
  photo intake. ALWAYS use this skill when Matt or Amy asks what something
  costs, where to buy something, what needs restocking, to plan the weekly
  order, to record that a product was good or bad, or sends a photo of a
  shelf or pantry. Also use for "run the cycle", "what's the basket",
  "is that a good price", "did prices go up", "we're out of X".
---

# Household pricing

Everything in this skill runs through `hazel.py`. **Do not do the arithmetic
yourself.** Ranking, unit-price math, threshold optimization and verdict
filtering are deterministic and live in code. A model doing this arithmetic
produces a confident wrong answer, and a confident wrong answer here is a
wrong purchase.

Working directory: `/opt/data/hazel/`
Entry point: `python3 /opt/data/hazel/hazel.py <command>`

## Hard rules

These are enforced in code. Do not attempt to work around them, and if a
command refuses, report the refusal rather than routing around it.

1. **Never purchase anything.** The cycle ends at a staged basket awaiting
   explicit approval. The sanctioned APIs cannot check out unattended anyway.
   Approval is a permanent architectural feature, not a training wheel.
2. **A rejected product is absent, not deprioritized.** `filter_candidates`
   removes it before ranking. Never re-suggest a rejected product because it
   became cheap. If Matt says something was bad, record a verdict.
3. **A low-confidence pack parse is not comparable.** If `unit_price` is
   null, say the comparison cannot be made. Do not fall back to comparing
   package prices — that silently compares a 12-pack against a 2-pack.
4. **Vision output is a proposal, never a fact.** Photo counts go to
   `pantry --proposals` and require confirmation. Never state pantry
   quantities derived from a photo as though they were known.
5. **Unit price is the series.** Package price is display only. It stops
   being comparable the moment a product line shrinks.

## Commands

```
hazel.py doctor                    verify keys, Kroger token, store, DB
hazel.py slots                     list need slots
hazel.py price <slot_id>           live price one slot across all rails
hazel.py cycle [--force] [--stage] full weekly run -> staged basket
hazel.py verdict <slot> <key> <keep|acceptable|rejected|untried> --reason "..."
hazel.py pantry                    on-hand, weeks left, restock flags
hazel.py pantry --set slot=units   record a stated count
hazel.py pantry --proposals        pending photo proposals
hazel.py pantry --confirm ID [--units N]
hazel.py alerts [--resolve ID --note "..."]
hazel.py backup [--prune]          export verdicts, roll up old observations
hazel.py approve [--dry-run]       submit the Kroger lines to a real cart
hazel.py links [--json]            one-click cart URLs for all three rails
hazel.py defer <slot> [--reason]   stop pricing a slot; --undo reverses
hazel.py switch <slot> --retailer  pin a slot to one rail; --clear releases
```

## Placing an order

Three rails, three different mechanisms. Run them in this order.

```
hazel.py cycle --stage      price everything, write a staged basket
hazel.py approve            push the Kroger lines into a real cart
hazel.py links              emit cart URLs for all three
```

`approve` is the ONLY sanctioned cart write. It fills the King Soopers cart
directly and defaults to PICKUP. Amazon and Walmart have no cart-write API,
so `links` builds URLs that fill those carts when clicked.

Hand Matt the output of `links` verbatim. Do not rewrite the URLs, do not
shorten them, do not wrap them in markdown. They are long and fragile.

**None of the three retailers reports a failed add.** A link that adds
nothing looks exactly like one that worked. Never tell Matt an order was
placed. Say the carts are ready and he should check the counts before
paying. Payment and pickup scheduling always happen in the vendor's own app.

Quantity is currently 1 on every line regardless of what the pantry says. If
he needs two of something, tell him to adjust it in the cart.

## Answering an out-of-stock alert

A cycle raises an alert when the cheapest option is unavailable. It names
the next-best in-stock alternative and the price difference. Two responses:

```
hazel.py defer <slot> --reason "wait for restock"
hazel.py switch <slot> --retailer walmart
```

`defer` stops pricing the slot until `--undo`. `switch` pins it to one rail
until `--clear`. Both persist across cycles, which is the point: editing a
basket by hand is undone by the next run.

Stock signal is not uniform. Walmart and Kroger report availability;
**Amazon reports nothing**. An Amazon line can be dead on arrival and Hazel
cannot know. Say so rather than implying the basket is verified.

## Need slots, not products

A slot is a FUNCTION the household needs filled, with an acceptance spec.
This is what lets Amazon Basics compete with Charmin on equal footing rather
than being invisible.

- `brand_policy: open` — anything meeting the spec competes
- `brand_policy: locked` — only `locked_brand` (use for coffee, things where
  the brand IS the requirement)
- `must_not_have` — hard disqualifiers, word-boundary matched

Cadence controls SerpApi spend. `weekly` for volatile and high-spend,
`monthly` for stable consumables, `quarterly` for the long tail. A slot is
only priced when due.

## The retailers, and why they differ

- **King Soopers** — Kroger official API, free, store-scoped to
  `HOUSEHOLD_ZIP`. Returns **promo pricing**, which nothing else does, plus
  real UPCs. Store `62000086` (Broomfield on Sheridan) for 80020.
- **Walmart** — SerpApi `engine=walmart`.
- **Amazon** — SerpApi `engine=amazon`. **Prices are logged-out**, so
  Subscribe & Save discounts are invisible and Amazon reads more expensive
  than it is. Mention this when Amazon narrowly loses.

Google Shopping was tested and rejected on 2026-08-08: it returned Sam's
Club, Office Depot, ULINE and marketplace resellers, with no King Soopers,
no Amazon and no Walmart first-party. Do not suggest it.

## Verdicts: the memory that matters

When Matt says a product was bad, immediately:

```
hazel.py verdict <slot> <product_key> rejected --reason "what he said"
```

This is data, not a lesson. **Do not route verdicts through the Notion
Learning Log.** A verdict written as prose in a skill file is a rule the
model can fail to weight; a verdict in the table cannot be argued with.

The Learning Log is for *patterns across* verdicts — "he rejects every
scented product", "under 15% saving he always declines untried brands".
Those generalize and belong in the loop.

Every verdict write exports the table to `/opt/data/hazel/backup/verdicts.json`.
That table is the one thing here that cannot be regenerated. Price history
rebuilds in a few cycles; months of living with products does not.

## Proposing an untried brand

An untried brand must clear a higher bar (15%) than a known one (10%),
because it is a gamble you have to live with, not just a price. When the
basket contains a line flagged `[UNTRIED BRAND]`, call it out explicitly and
ask before it ships. After delivery, ask how it was and record the verdict.

## Photo intake

When Matt sends a shelf photo, use the vision prompt in `pantry.py`
(`VISION_PROMPT`). Report only what is clearly visible, mark confidence, and
never estimate a shelf total. Feed the result through `ingest_photo_result`,
which creates proposals. Then ask him to confirm.

Unrecognized products are returned unmapped. Ask which slot they belong to.
Never guess a product into a slot.

## Alerting

Alerts are capped at three per cycle, ranked by dollar impact, and suppressed
for: slots not being bought, single observations, sub-$1 impact, and
low-confidence parses. If nothing clears the bar, say nothing. Alert fatigue
kills this feature faster than a bug will.

After a confirmed shrinkflation event the baseline is reset automatically. Do
not re-raise it.

## When something fails

`doctor` verifies live state. A slot-level failure does not abort the cycle —
the rest still stages, and errors are reported in the basket. Report failures
plainly rather than presenting a partial basket as complete.

Do not claim a price came from a retailer whose adapter errored.
