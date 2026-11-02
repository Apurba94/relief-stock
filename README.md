# Relief Stock

Warehouse tracking for a field store, on one offline computer: what came in, what went out, what is about to expire, and when to order more.

```
python -m relief_stock
```

Then open http://127.0.0.1:8034. It needs Python 3.10 or newer.

## What it does

- **Receipts and dispatches** with batches, expiry dates and waybill numbers
- **First expired, first out:** a dispatch gives a picking list by batch, soonest expiry first. Expired stock is never issued, and an over-issue is refused with the reason
- **Stock cards:** a running balance for each item, line by line, like a paper bin card
- **Losses** with a reason, and a report of the share lost
- **Physical counts:** enter what is on the shelf, and the differences are recorded openly as adjustments
- **Planning:** days of cover, reorder point and a suggested order, using usable stock only, so expired goods cannot look like supply

## Why a ledger

There is no stored "balance" field to fall out of step. Every balance is worked out from the history of movements, so any figure can be traced, and a correction is a new line and never an edit.

## How it is tested

Tests cover picking order, expiry on the day itself, stock with no expiry, and refusal of over-issues. A random test runs 200 dispatches and requires that no batch ever falls below zero and that the books always balance. Planning rules are tested with stock that has expired.

## Limits

One warehouse, one computer, and no barcode support. Reorder figures assume recent use continues, so they will lag behind a sudden emergency. Units are whatever you type, so keep them consistent.

## Tests

```
python -m unittest discover -s tests
```

MIT licence.
