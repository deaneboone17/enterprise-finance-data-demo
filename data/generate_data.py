"""Generate the synthetic CSVs for the demo.

Plain Python, standard library only. The output is deterministic: the same
seed always produces byte-identical files.

    python data/generate_data.py                 # writes data/raw/<table>/<table>.csv
    python data/generate_data.py --out /tmp/raw  # somewhere else
    python data/generate_data.py --seed 7        # a different dataset

All data is made up. There are no names, emails, addresses or card numbers.
IDs look like C0001 (customer) and P000001 (payment).

Edge cases the Silver layer is expected to handle:
- payments: exact duplicate rows (an ingest replay), failed attempts followed
  by a retry, retries that fail and end the subscription, and a few payments
  still "pending" at the end of the period.
- chargebacks: unresolved cases (blank resolved_ts, outcome "pending"), and a
  few cases that appear twice: first as pending, later as resolved.
- subscriptions: active rows have a blank end_date.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

DEFAULT_SEED = 42
N_CUSTOMERS = 1000
PERIOD_START = date(2024, 1, 1)
PERIOD_END = date(2025, 6, 30)  # last day of data

PLANS = [  # (plan, monthly price, weight)
    ("basic", 9.99, 0.45),
    ("standard", 19.99, 0.35),
    ("premium", 49.99, 0.20),
]
METHODS = [("card", 0.70), ("ach", 0.20), ("wallet", 0.10)]

FAIL_RATE = 0.06            # share of first attempts that fail
RETRY_FAIL_RATE = 0.30      # share of retries that fail too (subscription ends)
VOLUNTARY_CANCEL_RATE = 0.005  # chance per billing month that a customer cancels
DUPLICATE_RATE = 0.008      # share of payment rows replayed as exact duplicates
CHARGEBACK_RATE = 0.014    # share of settled payments that get a chargeback
PENDING_DAYS = 2            # payments in the last N days are still "pending"

CB_REASONS = [
    ("fraud", 0.30),
    ("not_recognized", 0.25),
    ("product_not_received", 0.15),
    ("duplicate_charge", 0.10),
    ("subscription_canceled", 0.20),
]
CB_OUTCOMES = [("won", 0.45), ("lost", 0.55)]
CB_SUPERSEDED_ROWS = 5      # resolved chargebacks that also have an earlier pending row

SUBSCRIPTION_COLS = ["customer_id", "plan", "start_date", "end_date", "amount", "status"]
PAYMENT_COLS = ["payment_id", "customer_id", "ts", "amount", "status", "method"]
CHARGEBACK_COLS = ["payment_id", "opened_ts", "reason", "resolved_ts", "outcome"]


def pick(rng: random.Random, weighted):
    values = [v for v, _ in weighted]
    weights = [w for _, w in weighted]
    return rng.choices(values, weights=weights, k=1)[0]


def add_months(d: date, months: int) -> date:
    """Same day of month, clamped to the month's last day."""
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def at_random_time(rng: random.Random, d: date) -> datetime:
    return datetime.combine(d, time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)))


def fmt_ts(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def generate(seed: int = DEFAULT_SEED):
    rng = random.Random(seed)
    subscriptions, payments, chargebacks = [], [], []
    pay_seq = 0
    last_ts = datetime.combine(PERIOD_END, time(23, 59, 59))

    def new_payment(cust, ts, amount, status, method):
        nonlocal pay_seq
        pay_seq += 1
        row = {
            "payment_id": f"P{pay_seq:06d}",
            "customer_id": cust,
            "ts": ts,
            "amount": amount,
            "status": status,
            "method": method,
        }
        payments.append(row)
        return row

    span_days = (date(2025, 3, 31) - PERIOD_START).days
    for i in range(1, N_CUSTOMERS + 1):
        cust = f"C{i:04d}"
        plan_name = pick(rng, [(p, w) for p, _, w in PLANS])
        price = next(pr for p, pr, _ in PLANS if p == plan_name)
        # Skew starts toward 2024 so most customers have a long payment history.
        start = PERIOD_START + timedelta(days=int(span_days * rng.random() ** 2.2))
        method = pick(rng, METHODS)

        end = None
        k = 0
        while True:
            bill_day = add_months(start, k)
            if bill_day > PERIOD_END:
                break
            ts = at_random_time(rng, bill_day)
            in_pending_window = bill_day > PERIOD_END - timedelta(days=PENDING_DAYS)
            if in_pending_window:
                new_payment(cust, ts, price, "pending", method)
            elif rng.random() < FAIL_RATE:
                new_payment(cust, ts, price, "failed", method)
                retry_day = bill_day + timedelta(days=rng.randint(1, 3))
                retry_ts = at_random_time(rng, min(retry_day, PERIOD_END))
                if rng.random() < RETRY_FAIL_RATE:
                    new_payment(cust, retry_ts, price, "failed", method)
                    end = min(retry_day + timedelta(days=1), PERIOD_END)  # involuntary churn
                    break
                new_payment(cust, retry_ts, price, "settled", method)
            else:
                new_payment(cust, ts, price, "settled", method)
            k += 1
            if rng.random() < VOLUNTARY_CANCEL_RATE:
                end = min(bill_day + timedelta(days=rng.randint(5, 25)), PERIOD_END)
                break

        subscriptions.append({
            "customer_id": cust,
            "plan": plan_name,
            "start_date": start.isoformat(),
            "end_date": end.isoformat() if end else "",
            "amount": price,
            "status": "cancelled" if end else "active",
        })

    # Chargebacks on settled payments, only if opened inside the period.
    for p in payments:
        if p["status"] != "settled" or rng.random() >= CHARGEBACK_RATE:
            continue
        opened = p["ts"] + timedelta(days=rng.randint(5, 60), hours=rng.randint(0, 23))
        if opened > last_ts:
            continue
        reason = pick(rng, CB_REASONS)
        resolved = opened + timedelta(days=rng.randint(10, 75), hours=rng.randint(0, 23))
        if resolved > last_ts:
            resolved, outcome = None, "pending"
        else:
            outcome = pick(rng, CB_OUTCOMES)
        chargebacks.append({
            "payment_id": p["payment_id"],
            "opened_ts": opened,
            "reason": reason,
            "resolved_ts": resolved,
            "outcome": outcome,
        })

    # A few resolved chargebacks were first delivered as "pending" rows.
    resolved_cbs = [c for c in chargebacks if c["resolved_ts"] is not None]
    for c in rng.sample(resolved_cbs, k=min(CB_SUPERSEDED_ROWS, len(resolved_cbs))):
        chargebacks.append({**c, "resolved_ts": None, "outcome": "pending"})

    # Ingest replays: exact duplicate payment rows.
    n_dupes = round(len(payments) * DUPLICATE_RATE)
    payments.extend(dict(p) for p in rng.sample(payments, k=n_dupes))

    payments.sort(key=lambda r: (r["ts"], r["payment_id"]))
    chargebacks.sort(key=lambda r: (r["opened_ts"], r["payment_id"], r["resolved_ts"] or datetime.max))
    return subscriptions, payments, chargebacks


def write_csv(path: Path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            out = {}
            for c in cols:
                v = r[c]
                if isinstance(v, datetime) or v is None:
                    v = fmt_ts(v)
                elif isinstance(v, float):
                    v = f"{v:.2f}"
                out[c] = v
            w.writerow(out)


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=here / "raw", help="output folder (default: data/raw)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    subs, pays, cbs = generate(args.seed)
    write_csv(args.out / "subscriptions" / "subscriptions.csv", SUBSCRIPTION_COLS, subs)
    write_csv(args.out / "payments" / "payments.csv", PAYMENT_COLS, pays)
    write_csv(args.out / "chargebacks" / "chargebacks.csv", CHARGEBACK_COLS, cbs)
    print(f"subscriptions: {len(subs)} rows")
    print(f"payments:      {len(pays)} rows")
    print(f"chargebacks:   {len(cbs)} rows")
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
