# System prices and imbalance volume

**Not ingested.**

The dataset is **`DISEBSP`**, served from `/balancing/settlement/system-prices`,
not `/datasets/DISEBSP`.

## In plain terms

**What it cost to fix the imbalance, and how far out the system was.**

When the grid does not balance on its own, the operator buys or sells the
difference. The price it ends up paying becomes the **system price**, and
anyone who was short pays it.

Two numbers matter here:

- **The system price** is exposed in both `systemBuyPrice` and
  `systemSellPrice`. Under the current single-price arrangements the two are
  equal. A high value is a market-stress signal.
- **`netImbalanceVolume`** says how short or long the system actually was, in
  MWh. [Positive means a short system, negative a long
  one](https://www.elexon.co.uk/bsc/settlement/imbalance-pricing/).

Of the two, **the volume is the more direct measure** for this project. Price
is the market's reaction to scarcity and is affected by bidding behaviour;
volume is the scarcity itself.

If shortfalls drive carbon forecast error, periods with large positive imbalance
volumes and high system prices should be the periods where carbon intensity
overshot its forecast.

---

## Endpoints

```
GET /balancing/settlement/system-prices/{settlementDate}
GET /balancing/settlement/system-prices/{settlementDate}/{settlementPeriod}
```

**There is no stream variant and no date range.** Requests are per settlement
date, so a year is 365 requests, unlike the `/datasets/*/stream` endpoints.

`settlementDate` must be exactly `yyyy-MM-dd`.

### Only the latest settlement run is returned

From the endpoint description:

> For each settlement period within the range, **only messages generated for
> the latest settlement run are returned.**

This is the same archive-it-forwards constraint found in B1610: both endpoints
serve the latest applicable settlement position rather than a recoverable
history of superseded runs.

Prices are produced by the SAA (Settlement Administration Agent) per settlement
run, and this endpoint hands back only the most recent. **Price revisions
cannot be reconstructed from history.**

Keeping price revisions needs the same approach as the carbon intensity
forecast: poll repeatedly and keep every version from the first poll onward.

There is also `/data-status/DISEBSP`, which reports publication status rather
than data.

## Response fields

| Field | Type | Notes |
|---|---|---|
| `settlementDate` | `str` | Local time date |
| `settlementPeriod` | `int` | 1 to 50 |
| `startTime` | `str` | Period start |
| `createdDateTime` | `str` | When this figure was produced |
| `systemSellPrice` | `float` | GBP/MWh |
| `systemBuyPrice` | `float` | GBP/MWh. Equal to `systemSellPrice` under single imbalance pricing |
| `bsadDefaulted` | `bool` | Balancing Services Adjustment Data defaulted |
| `priceDerivationCode` | `str` or `null` | How the price was arrived at, e.g. `P` |
| `reserveScarcityPrice` | `float` or `null` | Scarcity adder |
| **`netImbalanceVolume`** | `float` | MWh. **The system length or shortness** |
| `sellPriceAdjustment` | `float` or `null` | |
| `buyPriceAdjustment` | `float` or `null` | |
| `replacementPrice` | `float` or `null` | |
| `replacementPriceReferenceVolume` | `float` or `null` | |
| `totalAcceptedOfferVolume` | `float` or `null` | MWh the operator bought |
| `totalAcceptedBidVolume` | `float` or `null` | MWh the operator sold, negative |
| `totalAdjustmentSellVolume` | `float` or `null` | |
| `totalAdjustmentBuyVolume` | `float` or `null` | |
| `totalSystemTaggedAcceptedOfferVolume` | `float` or `null` | System-flagged subset |
| `totalSystemTaggedAcceptedBidVolume` | `float` or `null` | |
| `totalSystemTaggedAdjustmentSellVolume` | `float` or `null` | |
| `totalSystemTaggedAdjustmentBuyVolume` | `float` or `null` | |

## Things to know

**The "system tagged" fields are the useful split.** They separate volume
accepted for *system* reasons (network constraints, voltage) from volume
accepted for *energy* balancing. Same distinction as `soFlag` on BOALF, and the
same reason it matters: constraint actions are not evidence of energy shortfall.

**The first calculation is indicative and later refreshed.** [Elexon
publishes](https://bmrs.elexon.co.uk/system-prices) an indicative calculation
about 15 minutes after the settlement period and refreshes it at D+1 for late
actions or System Operator changes. This endpoint returns only the latest
available calculation.

**Use `numeric`, not `float`, in the table.** These are money and volume
figures that get aggregated.

**Nearly everything after the prices is nullable.** The spec marks **fourteen**
fields nullable: every adjustment, replacement, volume and system-tagged field,
plus `priceDerivationCode` and `reserveScarcityPrice`. Only the identifiers,
`startTime`, `createdDateTime`, the two prices, `bsadDefaulted` and
`netImbalanceVolume` are non-nullable. Build the table with nullable columns as
the default, not the exception.

## Observed live, 2026-08-20

One settlement period fetched
(`/balancing/settlement/system-prices/2026-08-01/5`, see
`tests/fixtures/elexon/system_prices.json`):

- **The response wraps rows in `{"metadata": {"datasets": ["DISEBSP"]}, "data": [...]}`.**
  Observed envelopes across the API: bare array (streams), `{data}` (dataset
  base endpoints), `{metadata, data}` (here).
- `systemSellPrice` equalled `systemBuyPrice` (160.0) in the observed period,
  consistent with GB's single imbalance price arrangements.
- `netImbalanceVolume` was positive (344.25 MWh) in a period where accepted
  offers exceeded accepted bids.
- `createdDateTime` was **the day after** the settlement date, so this sample
  was the D+1 refresh rather than the earlier indicative calculation.
- Values carry up to 18 decimal places.
