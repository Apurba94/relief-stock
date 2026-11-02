"""The offline web app."""
from datetime import date

from . import core
from .fieldkit import App, Fail, Raw, card, download, form, h, link, redirect, stats, table

SCHEMA = """
CREATE TABLE IF NOT EXISTS moves (id INTEGER PRIMARY KEY, day TEXT NOT NULL, item TEXT NOT NULL, batch TEXT NOT NULL, qty REAL NOT NULL, kind TEXT NOT NULL, expiry TEXT, reference TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS moves_item ON moves (item);
"""


def create_app(db="stock.db", today=date.today):
    a = App("Relief Stock", db, SCHEMA, [("Stock", "/"), ("Receive", "/receive"), ("Dispatch", "/dispatch"), ("Loss", "/loss"), ("Count", "/count")])

    def moves():
        return [core.Move(date.fromisoformat(r["day"]), r["item"], r["batch"], r["qty"], r["kind"], date.fromisoformat(r["expiry"]) if r["expiry"] else None, r["reference"], r["note"]) for r in a.rows("SELECT * FROM moves ORDER BY day, id")]

    def save(m):
        a.run("INSERT INTO moves (day, item, batch, qty, kind, expiry, reference, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", m.day.isoformat(), m.item, m.batch, m.qty, m.kind, m.expiry.isoformat() if m.expiry else None, m.reference, m.note)

    def items():
        return [r["item"] for r in a.rows("SELECT DISTINCT item FROM moves ORDER BY item")]

    @a.route("/")
    def home(req):
        ms = moves()
        now = today()
        rows = []
        for i in items():
            r = core.reorder(ms, i, now)
            rows.append({"item": i, **r, "total": core.balance(ms, i)})
        soon = core.expiring(ms, now)
        low = sum(1 for r in rows if r["order_now"])

        def cover(r):
            if r["days_of_cover"] is None:
                return "no recent use"
            cls = "bad" if r["order_now"] else ""
            return Raw(f'<span class="{cls}">{r["days_of_cover"]:.0f} days</span>')
        body = stats([("Items", len(rows)), ("Order now", low), ("Expiring within 60 days", len(soon))])
        body += card("Stock", table(rows, [("Item", lambda r: link(f"/item/{r['item']}", r["item"])), ("In store", lambda r: f"{r['total']:g}"), ("Usable", lambda r: f"{r['usable']:g}"), ("Use per day", lambda r: f"{r['daily_use']:.1f}"), ("Cover", cover), ("Suggested order", lambda r: f"{r['suggest']:.0f}" if r["order_now"] else "")], "Nothing received yet."))
        body += card("Expiring soon", table(soon, [("Item", lambda r: r[0]), ("Batch", lambda r: r[1]), ("Quantity", lambda r: f"{r[2]:g}"), ("Expires", lambda r: r[3].isoformat()), ("Days left", lambda r: Raw(f'<span class="{"bad" if r[4] < 0 else "warn"}">{r[4]}</span>'))], "Nothing is close to expiry."))
        return a.page("Stock", body)

    @a.route("/receive", ("GET", "POST"))
    def receive(req):
        if req.method == "POST":
            exp = req.date("expiry", False)
            save(core.Move(today(), req.text("item"), req.text("batch"), req.number("qty", low=0.001), "receipt", date.fromisoformat(exp) if exp else None, req.text("reference", False)))
            a.log("receive", req.text("item"))
            return redirect("/")
        return a.page("Receive", card("Goods received", form(a, "/receive", [("item", "Item"), ("batch", "Batch"), ("qty", "Quantity", "number"), ("expiry", "Expiry date", "date"), ("reference", "Waybill number")], "Receive")))

    @a.route("/dispatch", ("GET", "POST"))
    def dispatch(req):
        if req.method == "POST":
            try:
                out = core.dispatch(moves(), req.text("item"), req.number("qty", low=0.001), today(), req.text("reference", False))
            except core.StockError as exc:
                raise Fail(str(exc)) from None
            for m in out:
                save(m)
            a.log("dispatch", req.text("item"))
            lines = "".join(f"<li>Batch {h(m.batch)}: {-m.qty:g}</li>" for m in out)
            return a.page("Picking list", card(f"Pick {h(req.text('item'))} in this order", f"<ol>{lines}</ol>"))
        return a.page("Dispatch", card("Issue stock, soonest expiry first", form(a, "/dispatch", [("item", "Item", "select", [(i, i) for i in items()]), ("qty", "Quantity", "number"), ("reference", "Waybill number")], "Dispatch")))

    @a.route("/loss", ("GET", "POST"))
    def loss(req):
        if req.method == "POST":
            reason = req.text("reason")
            if reason not in core.LOSS_REASONS:
                raise Fail("Unknown reason")
            item, batch, q = req.text("item"), req.text("batch"), req.number("qty", low=0.001)
            have = core.batches(moves(), item).get(batch, (0, None))[0]
            if q > have + 1e-9:
                raise Fail(f"Batch {batch} holds only {have:g}")
            save(core.Move(today(), item, batch, -q, "loss", note=reason))
            return redirect("/")
        report = core.loss_report(moves())
        return a.page("Loss", card("Record a loss", form(a, "/loss", [("item", "Item", "select", [(i, i) for i in items()]), ("batch", "Batch"), ("qty", "Quantity", "number"), ("reason", "Reason", "select", [(r, r) for r in core.LOSS_REASONS])], "Record")) + card("Losses so far", table(list(report.items()), [("Item", lambda r: r[0]), ("Lost", lambda r: f"{r[1][0]:g}"), ("Share of receipts", lambda r: "" if r[1][1] is None else f"{r[1][1]:.1%}")], "No losses recorded.")))

    @a.route("/count", ("GET", "POST"))
    def count(req):
        ms = moves()
        lines = [(i, b, q) for i in items() for b, (q, _) in core.batches(ms, i).items() if abs(q) > 1e-9]
        if req.method == "POST":
            counted = {(i, b): req.number(f"c{k}", q, 0) for k, (i, b, q) in enumerate(lines)}
            out = core.reconcile(ms, counted, today())
            for m in out:
                save(m)
            return a.page("Count", card("Done", f"<p>{len(out)} adjustments were recorded.</p>"))
        return a.page("Physical count", card("Enter what is on the shelf", form(a, "/count", [(f"c{k}", f"{i}, batch {b} (ledger {q:g})", "number", q) for k, (i, b, q) in enumerate(lines)], "Save count")))

    @a.route("/item/<name>")
    def item(req, name):
        from urllib.parse import unquote
        name = unquote(name)
        rows = core.stock_card(moves(), name)
        if not rows:
            raise Fail("No such item", 404)
        return a.page(name, card("Stock card", table(rows, [("Date", lambda r: r[0].day.isoformat()), ("Type", lambda r: r[0].kind), ("Batch", lambda r: r[0].batch), ("Quantity", lambda r: f"{r[0].qty:+g}"), ("Balance", lambda r: f"{r[1]:g}"), ("Ref", lambda r: r[0].reference or r[0].note)])))

    @a.route("/ledger.csv")
    def ledger(req):
        return download(a.rows("SELECT day, item, batch, qty, kind, expiry, reference, note FROM moves ORDER BY id"), ["day", "item", "batch", "qty", "kind", "expiry", "reference", "note"], "ledger.csv")

    return a
