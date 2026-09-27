# Git

Bitcoin whale cohort tracking with "measurement illusion" detection.

## Stage 1 — `cohort_illusion_detection.py`
Groups wallets by the entity that owns them, treating custodians (Arkham labels, Coinbase Prime, EDGAR-disclosed custodians) separately. Flags an illusion event when raw whale accumulation diverges from the entity-adjusted balance by more than 1σ over a 30-day window. Run `python cohort_illusion_detection.py` to try it on sample data.

## Stage 2 — `data_pipeline.py`
Feeds live data into the Stage 1 tracker:

| Source | What it provides | Config |
|---|---|---|
| Esplora API (mempool.space by default) | Confirmed balances, per-transaction net flows | `--esplora-url` |
| Arkham Intelligence | Entity attribution; `cex`/`custodian`/`fund`/`etf` entities are treated as custodial | `ARKHAM_API_KEY` env var (optional) |
| Coinbase Prime list | Known custodial addresses (no public feed, so you supply a file) | `--coinbase-prime FILE` |
| SEC EDGAR | Custodians named in recent 10-K/10-Q/S-1/424B/8-K filings of spot BTC ETFs, plus any addresses in them | `--edgar-tickers IBIT,GBTC`, `SEC_USER_AGENT` env var (required by the SEC) |

Responses are cached in `.cache/` (balances 6h, labels 7d, filings 30d). Requests are throttled per host and retried with backoff on 429/5xx errors. Each wallet's balance is reconstructed to its value at the start of the window, then flows inside the window are replayed.

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name you@example.com"
export ARKHAM_API_KEY=...            # optional
python data_pipeline.py --addresses addresses.txt \
    --coinbase-prime coinbase_prime.txt --edgar-tickers IBIT,GBTC,FBTC \
    --days 30 --output flags.json
```

Address files have one address per line; `#` comments and extra CSV columns are ignored.

## Tests
```bash
python -m unittest discover -s tests
```
Tests run offline against mocked API responses.
