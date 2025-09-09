# Enterprise Finance Data Demo (Databricks, optional GCP)

[![Status](https://img.shields.io/badge/demo-ready-brightgreen)](./)
[![Databricks](https://img.shields.io/badge/platform-Databricks-red)](./)
[![Delta Lake](https://img.shields.io/badge/storage-Delta%20Lake-blue)](./)

**What this is:** a portfolio-ready data platform demo:
raw CSV ➜ **Bronze** (Delta) ➜ **Silver** (cleaned views) ➜ **Gold** KPIs (payments, revenue, chargeback rate).

📦 Data lives in GCS (Hybrid B+) or DBFS (Free Edition). Costs approximately a few dollars per month when using GCP.

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

</details>

### Option B — Hybrid B+ (GCP bucket + tiny cluster + SQL Serverless)
<details> 
<summary>Expand</summary>

Put CSVs in gs://🟩your-bucket/raw/{subscriptions,payments,chargebacks}/.

Store your GCP key in Databricks Secrets (gcp-secrets/gcs-key-json), then in a notebook:

with open("/local_disk0/gcp.json","w") as f: f.write(dbutils.secrets.get("gcp-secrets","gcs-key-json"))
spark.conf.set("spark.hadoop.google.cloud.auth.service.account.json.keyfile","/local_disk0/gcp.json")


Run Bronze/Silver on a single-node cluster in database demo_finance.

Create a SQL Serverless warehouse (Small, auto-stop 10m) and run 03_kpis_gold.sql for Gold.

</details>


### Architecture

  A[Raw CSVs (GCS or DBFS)] --> B[Bronze (Delta tables)]
  B --> C[Silver (typed/cleaned views)]
  C --> D[Gold KPIs (views)]
  D --> E[Notebook charts / SQL Serverless]
  

### What to demo live

1. Raw landing (GCS or DBFS) → same files can feed multiple engines.

2. Bronze: Delta tables, schema-on-read.

3. Silver: typed/cleaned views, simple quality (casts, null handling).

4. Gold: payments, settled/failed, gross revenue, chargeback rate.

5. Visuals: one payments trend, one chargeback rate chart.

6. Portability: flip source from DBFS → GCS with only path + creds changes.
   

### Costs (Hybrid B+)

1. Tiny all-purpose cluster for Bronze/Silver: ~1 DBU/run + small GCP VM fraction → about $1/run.

2. SQL Serverless for Gold (~10 min, Small): ~2 DBU/run → about $2/run.

3. 3–4 runs/month: typically ~$4–$12/month total.

4. Guardrails: auto-termination (10m), auto-stop (10m), job timeouts, minimal retries.
   

### Repo layout
infra/
  gcp_setup.md            # (optional) bucket + SA notes
  snowflake_setup.md      # (optional) external stage path
pipelines/
  databricks/
    01_ingest_bronze.py   # Python notebook (or copy into one)
    02_transform_silver.py
    03_kpis_gold.sql
  snowflake/
    stage_ingest.sql
    transform_core.sql
    kpis.sql
analytics/
  dashboard_walkthrough.md
scripts/
  upload_to_gcs.sh        # helper (edit BUCKET then run)
data/
  raw/                    # place CSVs here if using DBFS
assets/
  pipeline-architecture.png
  payments_trend.png
  chargeback_rate.png


### Next steps / Productionization

- Ingestion: switch CSV reads to Auto Loader for Bronze.

- Orchestration: Databricks Jobs or dbt / DLT; add SLAs.

- Data tests: null/duplicate checks, referential integrity.

- Lineage & docs: Unity Catalog, table comments, ownership.

- Cost: small node types, short auto-termination, few chunky runs.

- Parity: optional Snowflake external stage + COPY INTO to mirror KPIs.


### Credits

Built by YOUR NAME.
💼 LinkedIn: https://www.linkedin.com/in/deaneboone/

🧑‍💻 GitHub: https://github.com/deaneboone17

If this helped, a ⭐ on the repo is appreciated!



