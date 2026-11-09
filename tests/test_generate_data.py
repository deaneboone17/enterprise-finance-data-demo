"""Local checks for the synthetic data. No Spark or Databricks needed.

    python -m unittest discover tests      # or: pytest
"""

import csv
import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("generate_data", REPO / "data" / "generate_data.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

EXPECTED = {
    # table: (columns, min rows, max rows)
    "subscriptions": (gen.SUBSCRIPTION_COLS, 1000, 1000),
    "payments": (gen.PAYMENT_COLS, 11500, 13500),
    "chargebacks": (gen.CHARGEBACK_COLS, 120, 200),
}


def read(folder: Path, table: str):
    with (folder / table / f"{table}.csv").open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


class GeneratedData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        gen.main(["--out", str(cls.out)])
        cls.data = {t: read(cls.out, t) for t in EXPECTED}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_columns_and_row_counts(self):
        for t, (cols, lo, hi) in EXPECTED.items():
            header, rows = self.data[t]
            self.assertEqual(header, cols, t)
            self.assertTrue(lo <= len(rows) <= hi, f"{t}: {len(rows)} rows not in [{lo}, {hi}]")

    def test_committed_csvs_match_generator(self):
        for t in EXPECTED:
            committed = REPO / "data" / "raw" / t / f"{t}.csv"
            self.assertTrue(committed.exists(), f"missing {committed}; run python data/generate_data.py")
            fresh = self.out / t / f"{t}.csv"
            self.assertEqual(committed.read_bytes(), fresh.read_bytes(), f"{t}: committed CSV is stale")

    def test_types_parse(self):
        from datetime import date, datetime
        _, subs = self.data["subscriptions"]
        for r in subs:
            date.fromisoformat(r["start_date"])
            if r["end_date"]:
                date.fromisoformat(r["end_date"])
            float(r["amount"])
        _, pays = self.data["payments"]
        for r in pays:
            datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
            float(r["amount"])
        _, cbs = self.data["chargebacks"]
        for r in cbs:
            datetime.strptime(r["opened_ts"], "%Y-%m-%d %H:%M:%S")
            if r["resolved_ts"]:
                datetime.strptime(r["resolved_ts"], "%Y-%m-%d %H:%M:%S")

    def test_edge_cases_present(self):
        _, subs = self.data["subscriptions"]
        _, pays = self.data["payments"]
        _, cbs = self.data["chargebacks"]
        status = Counter(r["status"] for r in pays)
        self.assertGreater(status["failed"], 0, "no failed payments")
        self.assertGreater(status["pending"], 0, "no pending payments")
        dupes = sum(n - 1 for n in Counter(r["payment_id"] for r in pays).values() if n > 1)
        self.assertGreater(dupes, 0, "no duplicate payment rows")
        open_cbs = [r for r in cbs if not r["resolved_ts"]]
        self.assertTrue(open_cbs and all(r["outcome"] == "pending" for r in open_cbs), "unresolved chargebacks")
        keys = Counter((r["payment_id"], r["opened_ts"]) for r in cbs)
        self.assertTrue(any(n > 1 for n in keys.values()), "no superseded chargeback rows")
        self.assertTrue(any(not r["end_date"] for r in subs) and any(r["end_date"] for r in subs))
        self.assertEqual(len({r["customer_id"] for r in subs}), len(subs), "customer_id must be unique")

    def test_referential_integrity(self):
        _, subs = self.data["subscriptions"]
        _, pays = self.data["payments"]
        _, cbs = self.data["chargebacks"]
        customers = {r["customer_id"] for r in subs}
        settled = {r["payment_id"] for r in pays if r["status"] == "settled"}
        self.assertTrue(all(r["customer_id"] in customers for r in pays))
        self.assertTrue(all(r["payment_id"] in settled for r in cbs), "chargebacks only on settled payments")

    def test_no_personal_data(self):
        for t in EXPECTED:
            text = (self.out / t / f"{t}.csv").read_text()
            self.assertNotIn("@", text, f"{t}: looks like an email")

    def test_deterministic(self):
        a = gen.generate(gen.DEFAULT_SEED)
        b = gen.generate(gen.DEFAULT_SEED)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
