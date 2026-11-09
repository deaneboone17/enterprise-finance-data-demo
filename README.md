# Enterprise Finance Data Demo (Databricks)

A small subscription-billing pipeline on Databricks, built the medallion way:

raw CSV (Unity Catalog Volume) -> **Bronze** (Delta tables) -> **Silver** (typed, deduplicated views) -> **Gold** (KPI views: payments, revenue, chargeback rate)

Everything needed to run it is in this repo: a seeded data generator, the generated CSVs, three notebooks, and a script that runs the whole thing on a Databricks workspace as one job. The data is synthetic. There are no real names, emails or card numbers.

## Quickstart

You need a Databricks workspace with Unity Catalog and serverless compute. The tested run used a Databricks workspace on Google Cloud. [Databricks Free Edition](https://www.databricks.com/learn/free-edition) is serverless with Unity Catalog, so it should work there too, but that was not tested.

### Option 1: one command from your machine

```bash
pip install -r requirements.txt
# auth: either `databricks auth login`, a profile in ~/.databrickscfg,
# or DATABRICKS_HOST and DATABRICKS_TOKEN in the environment
python scripts/run_on_databricks.py
```

The script:

1. creates schema `demo_finance` and a managed Volume `raw` in your default catalog (if missing),
2. uploads `data/raw/*/*.csv` to `/Volumes/<catalog>/demo_finance/raw/`,
3. imports the notebooks to `/Users/<you>/enterprise-finance-data-demo`,
4. runs them in order as one serverless job (bronze, then silver, then gold),
5. prints each task's status and the Gold tables.

Useful flags: `--catalog`, `--schema`, `--profile`, `--generate` (rebuild the CSVs first), `--dedup none` (see what duplicates do to the KPIs), `--save-outputs` (write the executed notebooks, with outputs, back into `databricks/`). Run `python scripts/run_on_databricks.py --help` for all of them.

A full run takes about 2 to 3 minutes, most of it serverless start-up.

### Option 2: by hand in the workspace

1. Create a schema and a Volume, for example `demo_finance` and `raw` (Catalog Explorer, or `CREATE SCHEMA demo_finance; CREATE VOLUME demo_finance.raw;`).
2. Upload the three CSVs into the Volume so it looks like `/Volumes/<catalog>/demo_finance/raw/subscriptions/subscriptions.csv` (same for `payments` and `chargebacks`).
3. Import the notebooks from `databricks/` and run `01_ingest_bronze`, `02_transform_silver`, `03_kpis_gold` in order. The widget defaults work if you used the names above. `CATALOG` blank means your default catalog.
4. Query the results:
   ```sql
   USE demo_finance;
   SELECT * FROM gold_kpi_monthly ORDER BY month;
   SELECT * FROM gold_revenue_by_plan ORDER BY month, plan;
   ```

### The data

The CSVs are committed, so you can skip this. To rebuild them (deterministic, same seed gives identical files):

```bash
python data/generate_data.py            # writes data/raw/<table>/<table>.csv
python data/generate_data.py --seed 7   # a different dataset
python -m unittest discover tests       # schema, row count and edge-case checks, no Spark needed
```

| File | Rows | Columns |
|---|---|---|
| `subscriptions.csv` | 1,000 | customer_id, plan, start_date, end_date, amount, status |
| `payments.csv` | 12,722 | payment_id, customer_id, ts, amount, status, method |
| `chargebacks.csv` | 161 | payment_id, opened_ts, reason, resolved_ts, outcome |

Monthly billing from January 2024 to June 2025 across three plans (basic 9.99, standard 19.99, premium 49.99). The generator deliberately includes problems the pipeline has to handle:

- 101 exact duplicate payment rows (an ingest replay),
- failed payments, each followed by a retry; a retry that also fails ends the subscription,
- 49 payments still `pending` at the end of the period,
- chargebacks still open (blank `resolved_ts`, outcome `pending`),
- 5 chargebacks that arrive twice: first as pending, later as resolved.

## What each layer does

**Bronze** (`databricks/01_ingest_bronze.ipynb`). Reads each CSV folder with every column as a string and writes it unchanged to a Delta table (`bronze_subscriptions`, `bronze_payments`, `bronze_chargebacks`), plus two lineage columns: `_source_file` and `_ingested_at`. It checks the header against the expected columns and fails fast if a file does not match. Tables are overwritten each run, so reruns are safe.

**Silver** (`databricks/02_transform_silver.ipynb`). Views that cast to proper types (dates, timestamps, doubles), turn blanks into NULL, and add a `yyyy-MM` month column. With `DEDUP_STRATEGY=keyed` (the default) it keeps one payment per `payment_id` and one chargeback per (`payment_id`, `opened_ts`), preferring the resolved row. It ends with quality checks that fail the run on NULL keys or leftover duplicate keys.

**Gold** (`databricks/03_kpis_gold.ipynb`). KPI views on top of Silver:

| View | Grain | Measures |
|---|---|---|
| `gold_payments_monthly` | month | payments, settled, failed, gross_revenue (settled amount) |
| `gold_chargeback_rate` | month | chargebacks opened, settled, chargeback_rate = chargebacks / settled |
| `gold_revenue_by_plan` | month, plan | settled_payments, gross_revenue, chargeback_losses (settled amount later lost to a chargeback), net_revenue |
| `gold_kpi_monthly` | month | the two monthly views joined, plus failure_rate = failed / payments |

Gold is views rather than tables: they always match Silver and cost nothing to store. At this size that is the right trade. At real volume you would materialize them.

## Sample output (from a real run)

Silver quality step: payments 12,722 Bronze rows -> 12,621 Silver rows (101 duplicates removed); chargebacks 161 -> 156 (5 superseded pending rows removed); all NULL and duplicate-key checks pass.

`gold_kpi_monthly`:

| month | payments | settled | failed | gross_revenue | chargebacks | chargeback_rate | failure_rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024-01 | 309 | 285 | 24 | 6257.15 | 0 | 0.0 | 0.0777 |
| 2024-02 | 408 | 378 | 30 | 8476.22 | 2 | 0.0053 | 0.0735 |
| 2024-03 | 502 | 465 | 37 | 10225.35 | 3 | 0.0065 | 0.0737 |
| 2024-04 | 553 | 520 | 33 | 11424.8 | 8 | 0.0154 | 0.0597 |
| 2024-05 | 624 | 569 | 55 | 12404.31 | 8 | 0.0141 | 0.0881 |
| 2024-06 | 655 | 606 | 49 | 13253.94 | 7 | 0.0116 | 0.0748 |
| 2024-07 | 703 | 656 | 47 | 14333.44 | 5 | 0.0076 | 0.0669 |
| 2024-08 | 727 | 679 | 48 | 14863.21 | 9 | 0.0133 | 0.066 |
| 2024-09 | 747 | 699 | 48 | 15343.01 | 7 | 0.01 | 0.0643 |
| 2024-10 | 789 | 713 | 76 | 15572.87 | 7 | 0.0098 | 0.0963 |
| 2024-11 | 803 | 741 | 62 | 16302.59 | 8 | 0.0108 | 0.0772 |
| 2024-12 | 807 | 772 | 35 | 16962.28 | 15 | 0.0194 | 0.0434 |
| 2025-01 | 839 | 787 | 52 | 17552.13 | 15 | 0.0191 | 0.062 |
| 2025-02 | 845 | 786 | 59 | 17562.14 | 11 | 0.014 | 0.0698 |
| 2025-03 | 871 | 795 | 76 | 17602.05 | 11 | 0.0138 | 0.0873 |
| 2025-04 | 842 | 765 | 77 | 17032.35 | 12 | 0.0157 | 0.0914 |
| 2025-05 | 814 | 755 | 59 | 16852.45 | 16 | 0.0212 | 0.0725 |
| 2025-06 | 783 | 689 | 45 | 15363.11 | 12 | 0.0174 | 0.0575 |

`payments` includes pending rows, so in June 2025 settled + failed is less than payments.

`gold_revenue_by_plan`, summed over all 18 months:

| plan | settled_payments | gross_revenue | chargeback_losses | net_revenue |
|---|---:|---:|---:|---:|
| basic | 5,106 | 51,008.94 | 319.68 | 50,689.26 |
| premium | 2,512 | 125,574.88 | 699.86 | 124,875.02 |
| standard | 4,042 | 80,799.58 | 539.73 | 80,259.85 |
| **total** | 11,660 | 257,383.40 | 1,559.27 | 255,824.13 |

Losses in the last two months are zero because those chargebacks are still open. The notebooks in `databricks/` are committed with the outputs of the tested run, so you can read every step on GitHub. That run used the schema name `demo_finance_runnable`; catalog and user names in the outputs are replaced with placeholders.

## Repo layout

```
data/
  generate_data.py        seeded generator (standard library only)
  raw/                    the generated CSVs, one folder per table
databricks/
  01_ingest_bronze.ipynb
  02_transform_silver.ipynb
  03_kpis_gold.ipynb
scripts/
  run_on_databricks.py    upload, import, run as a job, print Gold
tests/
  test_generate_data.py   local checks on the generated data
infra/
  gcp_setup.md            optional GCS landing zone (not exercised in the tested run)
requirements.txt          databricks-sdk, for the runner only
```

## Optional: read from Google Cloud Storage instead

The notebooks read from whatever folder `RAW_DIR` points to, so a `gs://` path works once Databricks can read the bucket. `infra/gcp_setup.md` describes the setup. **This path was not exercised in the tested run**; only the Volume path was.

## Cost

On serverless, one run is a few minutes of compute. No cluster or SQL warehouse is created. To clean up, drop the schema (`DROP SCHEMA demo_finance CASCADE`, which also removes the Volume and its files) and delete the workspace folder.

## Ideas for next steps

- Auto Loader for incremental Bronze ingest instead of full overwrites.
- Lakeflow Declarative Pipelines (DLT) with expectations in place of the hand-written Silver checks.
- Materialized Gold tables with a refresh schedule once volume grows.
- Table and column comments plus ownership in Unity Catalog.

Built by Deane Boone. [LinkedIn](https://www.linkedin.com/in/deaneboone/)
