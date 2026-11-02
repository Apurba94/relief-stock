"""Warehouse rules. Every movement is a ledger line, and balances are always worked out from it,
so the record cannot drift from its history. Batches are picked first-expired-first-out."""
from dataclasses import dataclass
from datetime import date, timedelta


class StockError(Exception):
    pass


@dataclass
class Move:
    day: date
    item: str
    batch: str
    qty: float            # positive in, negative out
    kind: str             # receipt, dispatch, loss or count
    expiry: date = None
    reference: str = ""
    note: str = ""


LOSS_REASONS = ("damaged", "expired", "theft", "spoiled", "count difference")


def batches(moves, item):
    """{batch: (balance, expiry)} for one item, from the ledger."""
    out = {}
    for m in moves:
        if m.item == item:
            bal, exp = out.get(m.batch, (0.0, None))
            out[m.batch] = (bal + m.qty, m.expiry or exp)
    return out


def balance(moves, item):
    return sum(v[0] for v in batches(moves, item).values())


def pick(moves, item, qty, today):
    """Choose batches for a dispatch, soonest expiry first. Expired stock is never picked.
    Stock with no expiry date goes last. Returns [(batch, quantity)]."""
    if qty <= 0:
        raise StockError("quantity must be above zero")
    usable = [(e or date.max, b, q) for b, (q, e) in batches(moves, item).items() if q > 1e-9 and (e is None or e >= today)]
    usable.sort(key=lambda t: (t[0], t[1]))
    available = sum(t[2] for t in usable)
    if available + 1e-9 < qty:
        expired = sum(q for q, e in batches(moves, item).values() if q > 0 and e and e < today)
        note = f" ({expired:g} more is expired)" if expired else ""
        raise StockError(f"only {available:g} of {item} can be issued{note}, and {qty:g} was requested")
    plan, left = [], qty
    for _, b, q in usable:
        take = min(q, left)
        plan.append((b, take))
        left -= take
        if left <= 1e-9:
            break
    return plan


def dispatch(moves, item, qty, today, reference=""):
    out = []
    for b, q in pick(moves, item, qty, today):
        out.append(Move(today, item, b, -q, "dispatch", reference=reference))
    return out


def stock_card(moves, item):
    """The running balance, line by line, as on a paper bin card."""
    rows = []
    total = 0.0
    for m in sorted((m for m in moves if m.item == item), key=lambda m: m.day):
        total += m.qty
        rows.append((m, total))
    return rows


def expiring(moves, today, days=60):
    """Batches with stock that expire soon, or have expired, soonest first."""
    rows = []
    for item in {m.item for m in moves}:
        for b, (q, e) in batches(moves, item).items():
            if q > 1e-9 and e and (e - today).days <= days:
                rows.append((item, b, q, e, (e - today).days))
    return sorted(rows, key=lambda r: r[3])


def daily_use(moves, item, today, days=60):
    start = today - timedelta(days=days)
    out = -sum(m.qty for m in moves if m.item == item and m.kind == "dispatch" and start < m.day <= today)
    return out / days


def reorder(moves, item, today, lead_days=14, safety_days=7):
    """When to order. Cover counts usable stock only, so expired stock cannot look like supply."""
    use = daily_use(moves, item, today)
    usable = sum(q for q, e in batches(moves, item).values() if q > 0 and (e is None or e >= today))
    point = use * (lead_days + safety_days)
    cover = usable / use if use > 0 else None
    return {"usable": usable, "daily_use": use, "days_of_cover": cover, "reorder_point": point, "order_now": use > 0 and usable <= point, "suggest": max(0.0, use * 60 - usable) if use > 0 else 0.0}


def reconcile(moves, counted, today):
    """Compare a physical count with the ledger. counted: {(item, batch): quantity}. Returns adjusting moves."""
    out = []
    for (item, b), q in counted.items():
        have = batches(moves, item).get(b, (0.0, None))[0]
        if abs(have - q) > 1e-9:
            out.append(Move(today, item, b, q - have, "count", note=f"counted {q:g}, ledger said {have:g}"))
    return out


def loss_report(moves):
    """Losses by item, with the share of everything received."""
    received, lost = {}, {}
    for m in moves:
        if m.kind == "receipt":
            received[m.item] = received.get(m.item, 0) + m.qty
        elif m.kind == "loss" or (m.kind == "count" and m.qty < 0):
            lost[m.item] = lost.get(m.item, 0) - m.qty
    return {k: (v, v / received[k] if received.get(k) else None) for k, v in lost.items()}
