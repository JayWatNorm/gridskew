# System prices and imbalance volume

**Not yet ingested.** Planned for Phase 1, step 3 of the thesis.

Historically referred to as **DISEBSP**. It is not at `/datasets/DISEBSP`.

## In plain terms

**What it cost to fix the imbalance, and how far out the system was.**

When the grid does not balance on its own, the operator buys or sells the
difference. The price it ends up paying becomes the **system price**, and
anyone who was short pays it.

Two numbers matter here:

- **`systemBuyPrice`** spikes when the system is short of electricity. It is a
  stress signal: high prices mean the operator was scrambling.
- **`netImbalanceVolume`** says how short or long the system actually was, in
  MWh. Positive and negative have opposite meanings.

Of the two, **the volume is the more direct measure** for this project. Price
is the market's reaction to scarcity and is affected by bidding behaviour;
volume is the scarcity itself.

If the thesis holds, periods with large negative imbalance volumes and high buy
prices should be the periods where carbon intensity overshot its forecast.

---

## Endpoints

```
GET /balancing/settlement/system-prices/{settlementDate}
GET /balancing/settlement/system-prices/{settlementDate}/{settlementPeriod}
```

**Note there is no stream variant and no date range.** This is per settlement
date, so ingesting a year means a 365-iteration day loop. Different pattern
from the `/datasets/*/stream` endpoints.

`settlementDate` must be exactly `yyyy-MM-dd`.

### Only the latest settlement run is returned

From the endpoint description:

> For each settlement period within the range, **only messages generated for
> the latest settlement run are returned.**

This is a meaningful limitation and it differs from B1610, where every run is
retrievable via `settlementRunType`.

Prices are produced by the SAA (Settlement Administration Agent) per settlement
run, and this endpoint hands back only the most recent. **Price revisions
cannot be reconstructed from history.**

If revision history matters for prices, it has the same archive-it-yourself
shape as the carbon intensity forecast: poll repeatedly and keep every version,
starting from whenever you notice. Worth deciding deliberately rather than
discovering later.

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
| `systemBuyPrice` | `float` | GBP/MWh. Equal to the sell price in the spec's own example. Whether they routinely differ is an open question below |
| `bsadDefaulted` | `bool` | Balancing Services Adjustment Data defaulted |
| `priceDerivationCode` | `str` | How the price was arrived at, e.g. `P` |
| `reserveScarcityPrice` | `float` | Scarcity adder |
| **`netImbalanceVolume`** | `float` | MWh. **The system length or shortness** |
| `sellPriceAdjustment` | `float` | |
| `buyPriceAdjustment` | `float` | |
| `replacementPrice` | `float` or `null` | |
| `replacementPriceReferenceVolume` | `float` or `null` | |
| `totalAcceptedOfferVolume` | `float` | MWh the operator bought |
| `totalAcceptedBidVolume` | `float` | MWh the operator sold, negative |
| `totalAdjustmentSellVolume` | `float` | |
| `totalAdjustmentBuyVolume` | `float` | |
| `totalSystemTaggedAcceptedOfferVolume` | `float` | System-flagged subset |
| `totalSystemTaggedAcceptedBidVolume` | `float` | |
| `totalSystemTaggedAdjustmentSellVolume` | `float` or `null` | |
| `totalSystemTaggedAdjustmentBuyVolume` | `float` | |

## Things to know

**The "system tagged" fields are the useful split.** They separate volume
accepted for *system* reasons (network constraints, voltage) from volume
accepted for *energy* balancing. Same distinction as `soFlag` on BOALF, and the
same reason it matters: constraint actions are not evidence of energy shortfall.

**`createdDateTime` suggests revision.** Prices are indicative at first and
firmed up later. Not yet confirmed how often they change.

**Use `numeric`, not `float`, in the table.** These are money and volume
figures that get aggregated.

**Nearly everything after the prices is nullable.** The table above marks three
fields "or null"; the spec marks **fourteen** nullable — every adjustment,
volume and system-tagged field, plus `priceDerivationCode` and
`reserveScarcityPrice`. Only the identifiers, `startTime`, `createdDateTime`,
the two prices, `bsadDefaulted` and `netImbalanceVolume` are non-nullable.
Build the table with nullable columns as the default, not the exception.

## Observed live, 2026-08-20

One settlement period fetched
(`/balancing/settlement/system-prices/2026-08-01/5`, see
`tests/fixtures/elexon/system_prices.json`):

- **The response wraps rows in `{"metadata": {"datasets": ["DISEBSP"]}, "data": [...]}`**,
  a different envelope again from the datasets endpoints. Three envelope shapes
  now observed across the API: bare array (streams), `{data}` (dataset base
  endpoints), `{metadata, data}` (here).
- `systemSellPrice` equalled `systemBuyPrice` (160.0) in the observed period,
  consistent with GB's single imbalance price arrangements.
- `netImbalanceVolume` was positive (344.25 MWh) in a period where accepted
  offers exceeded accepted bids.
- `createdDateTime` was **the day after** the settlement date, so these figures
  are produced on roughly a T+1 schedule, not in real time.
- Values carry up to 18 decimal places. `numeric`, not `float`, confirmed.

## Open questions

- How often does `systemBuyPrice` differ from `systemSellPrice`, if ever, under
  the current single-price arrangements?
- Sign convention on `netImbalanceVolume`: positive alongside offer-heavy
  volumes here suggests positive means the system was short, but one
  observation is not a convention. Confirm against a period with the opposite
  imbalance.
- Are these figures revised as settlement runs advance? Only the latest run is
  served, so revision would be invisible without polling.
