# Enterprise Finance Data Demo (Databricks, optional GCP)

[![Status](https://img.shields.io/badge/demo-ready-brightgreen)](./)
[![Databricks](https://img.shields.io/badge/platform-Databricks-red)](./)
[![Delta Lake](https://img.shields.io/badge/storage-Delta%20Lake-blue)](./)

**What this is:** a portfolio-ready data platform demo:
raw CSV ➜ **Bronze** (Delta) ➜ **Silver** (cleaned views) ➜ **Gold** KPIs (payments, revenue, chargeback rate).

📦 Data lives in GCS or DBFS (Free Edition). Costs approximately a few dollars per month when using GCP.

---

## Quickstart

### Option A — Databricks Free Edition (no cloud cost)
<details>
<summary>Expand</summary>

1. Upload the three CSVs to DBFS:
   - `/FileStore/tables/raw/subscriptions.csv`
   - `/FileStore/tables/raw/payments.csv`
   - `/FileStore/tables/raw/chargebacks.csv`
2. Run the notebooks / cells in `pipelines/databricks`:
   - `01_ingest_bronze.py` ➜ creates `bronze_*` Delta tables  
   - `02_transform_silver.py` ➜ creates `silver_*` views  
   - `03_kpis_gold.sql` ➜ creates `gold_*` views
3. Open a SQL cell and run:
   ```sql
   USE demo_finance;
   SELECT * FROM gold_payments_monthly ORDER BY month;
   SELECT * FROM gold_chargeback_rate ORDER BY month;
