# GCP Setup (for Databricks or local-free variant)

  

This guide prepares **Google Cloud Storage (GCS)** as your raw landing zone and a **least-privilege** service account for Databricks to read those files. It also shows how to store the key in **Databricks Secrets** (no DBFS), and how to configure a notebook to read from `gs://…`.

  

> If you’re using **Databricks Free Edition (no cloud)**, you can skip GCP and upload CSVs to `dbfs:/FileStore/tables/raw/`.

  

---

  

## Prerequisites

  

- Google Cloud SDK installed (`gcloud`, `gsutil`)

- A GCP project you can use (example: `de-ai-portfolio`)

- Your Databricks (GCP) workspace URL and a Personal Access Token (PAT)

- The three CSVs locally: `subscriptions.csv`, `payments.csv`, `chargebacks.csv`

  

---

  

## 1) Set project and choose a region

  

> Use the **same region** as your Databricks workspace (example: `us-central1`).

  

**PowerShell**

```powershell

gcloud init

gcloud config set project de-ai-portfolio

$PROJECT = (gcloud config get-value project)

$REGION = "us-central1"
```
  

**bash**
```
gcloud init

gcloud config set project de-ai-portfolio

PROJECT="$(gcloud config get-value project)"

REGION="us-central1"
```
---  

## 2) Create a globally-unique GCS bucket

  

**PowerShell**
```
$BUCKET = "de-portfolio-$(Get-Date -Format yyyyMMdd)-$([System.Guid]::NewGuid().ToString('N').Substring(0,6))"

gsutil mb -p $PROJECT -c STANDARD -l $REGION "gs://$BUCKET/"
```
  

**bash**
```
BUCKET="de-portfolio-$(date +%Y%m%d)-$RANDOM"

gsutil mb -p "$PROJECT" -c STANDARD -l "$REGION" "gs://$BUCKET/"
```
--- 

## 3) Upload the demo CSVs

**Bucket Layout**
```
gs://<bucket>/raw/subscriptions/subscriptions.csv

gs://<bucket>/raw/payments/payments.csv

gs://<bucket>/raw/chargebacks/chargebacks.csv
```
  

**PowerShell**
```
### Adjust the local paths as needed

gsutil cp .\enterprise-demo\data\raw\subscriptions.csv "gs://$BUCKET/raw/subscriptions/"

gsutil cp .\enterprise-demo\data\raw\payments.csv "gs://$BUCKET/raw/payments/"

gsutil cp .\enterprise-demo\data\raw\chargebacks.csv "gs://$BUCKET/raw/chargebacks/"

  

gsutil ls "gs://$BUCKET/raw/"
```
  

**bash**
```
gsutil cp ./enterprise-demo/data/raw/subscriptions.csv "gs://$BUCKET/raw/subscriptions/"

gsutil cp ./enterprise-demo/data/raw/payments.csv "gs://$BUCKET/raw/payments/"

gsutil cp ./enterprise-demo/data/raw/chargebacks.csv "gs://$BUCKET/raw/chargebacks/"

  

gsutil ls "gs://$BUCKET/raw/"

 ``` 
---
## 4) Create a read-only service account + key

Role: roles/storage.objectViewer (minimal read access).

  

**PowerShell**
```
gcloud iam service-accounts create databricks-gcs-reader `

--display-name "Databricks GCS Reader"

  

$SA_EMAIL = "databricks-gcs-reader@$PROJECT.iam.gserviceaccount.com"

  

### Project-level read (simplest)

gcloud projects add-iam-policy-binding $PROJECT `

--member="serviceAccount:$SA_EMAIL" `

--role="roles/storage.objectViewer"

  

### Optional: bucket-level binding (redundant if project-level is set)

gsutil iam ch "serviceAccount:$SA_EMAIL:objectViewer" "gs://$BUCKET"

  

### Create a JSON key locally (keep safe, do NOT commit)

gcloud iam service-accounts keys create gcp.json `

--iam-account="$SA_EMAIL"
```
  

**bash**
```
gcloud iam service-accounts create databricks-gcs-reader \

--display-name "Databricks GCS Reader"

  

SA_EMAIL="databricks-gcs-reader@$PROJECT.iam.gserviceaccount.com"

  

### Project-level read (simplest)

gcloud projects add-iam-policy-binding "$PROJECT" \

--member="serviceAccount:$SA_EMAIL" \

--role="roles/storage.objectViewer"

  

### Optional: bucket-level binding (redundant if project-level is set)

gsutil iam ch "serviceAccount:$SA_EMAIL:objectViewer" "gs://$BUCKET"

  

### Create a JSON key locally (keep safe, do NOT commit)

gcloud iam service-accounts keys create gcp.json \

--iam-account="$SA_EMAIL"
```
---  

## 5) Store the key in Databricks Secrets (no DBFS)

  

Option A — PowerShell via Databricks REST API (no pip required)

  

1. In your workspace, create a PAT (User settings → Developer → Access tokens).

  

2. Use these commands to create a scope and store the key as bytes:

  

### EDIT your host + paste your PAT
```
$DATABRICKS_HOST = "https://<your-workspace>.gcp.databricks.com"

$DATABRICKS_TOKEN = "<PASTE_TOKEN>"

$Headers = @{ Authorization = "Bearer $DATABRICKS_TOKEN" }
```
  

### Create scope (Databricks-backed)
```
$BodyCreate = @{ scope = "gcp-secrets"; initial_manage_principal = "users" } | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$DATABRICKS_HOST/api/2.0/secrets/scopes/create" -Headers $Headers -ContentType "application/json" -Body $BodyCreate
```
  

### Put key as bytes_value (base64)
```
$JsonPath = (Resolve-Path .\gcp.json).Path

$bytes = [System.IO.File]::ReadAllBytes($JsonPath)

$base64 = [System.Convert]::ToBase64String($bytes)

$BodyPut = @{ scope="gcp-secrets"; key="gcs-key-json"; bytes_value=$base64 } | ConvertTo-Json -Compress

Invoke-RestMethod -Method Post -Uri "$DATABRICKS_HOST/api/2.0/secrets/put" -Headers $Headers -ContentType "application/json" -Body $BodyPut
```
  

### Verify (value is hidden)
```
Invoke-RestMethod -Method Get -Uri "$DATABRICKS_HOST/api/2.0/secrets/list?scope=gcp-secrets" -Headers $Headers
```
  

Option B — bash with curl
```
DATABRICKS_HOST="https://<your-workspace>.gcp.databricks.com"

DATABRICKS_TOKEN="<PASTE_TOKEN>"

  

### Create scope

curl -sS -X POST "$DATABRICKS_HOST/api/2.0/secrets/scopes/create" \

-H "Authorization: Bearer $DATABRICKS_TOKEN" \

-H "Content-Type: application/json" \

-d '{"scope":"gcp-secrets","initial_manage_principal":"users"}'

  

### Put key as bytes_value (base64)

BASE64=$(base64 -w 0 gcp.json 2>/dev/null || base64 gcp.json)

curl -sS -X POST "$DATABRICKS_HOST/api/2.0/secrets/put" \

-H "Authorization: Bearer $DATABRICKS_TOKEN" \

-H "Content-Type: application/json" \

-d "{\"scope\":\"gcp-secrets\",\"key\":\"gcs-key-json\",\"bytes_value\":\"$BASE64\"}"

  

### Verify

curl -sS -X GET "$DATABRICKS_HOST/api/2.0/secrets/list?scope=gcp-secrets" \

-H "Authorization: Bearer $DATABRICKS_TOKEN"
```
---  

## 6) Configure a Databricks notebook to read gs://…

  

Works for non-UC (hive_metastore) and UC (main). Use UC if you plan to run SQL Serverless later.

### --- Pull key from Secrets -> write to local disk (no DBFS)
```
scope = "gcp-secrets"

key = "gcs-key-json"

  

creds_json = dbutils.secrets.get(scope=scope, key=key)

with open("/local_disk0/gcp.json", "w") as f:

f.write(creds_json)

  

### --- Force the GCS connector to use this key (both property families + ADC)

import os

PROJECT_ID = "<your-project-id>" # optional but good for logs

KEYFILE = "/local_disk0/gcp.json"

  

spark.conf.set("spark.hadoop.google.cloud.auth.service.account.enable", "true")

spark.conf.set("spark.hadoop.google.cloud.auth.service.account.json.keyfile", KEYFILE)

  

spark.conf.set("spark.hadoop.fs.gs.auth.service.account.enable", "true")

spark.conf.set("spark.hadoop.fs.gs.auth.service.account.json.keyfile", KEYFILE)

spark.conf.set("spark.hadoop.fs.gs.project.id", PROJECT_ID)

  

spark.conf.set("spark.driverEnv.GOOGLE_APPLICATION_CREDENTIALS", KEYFILE)

spark.conf.set("spark.executorEnv.GOOGLE_APPLICATION_CREDENTIALS", KEYFILE)

  

### Also push into live Hadoop conf

hconf = sc._jsc.hadoopConfiguration()

hconf.set("google.cloud.auth.service.account.enable", "true")

hconf.set("google.cloud.auth.service.account.json.keyfile", KEYFILE)

hconf.set("fs.gs.auth.service.account.enable", "true")

hconf.set("fs.gs.auth.service.account.json.keyfile", KEYFILE)

hconf.set("fs.gs.project.id", PROJECT_ID)

  

### --- Choose catalog/schema

### Non-UC (default)

spark.sql("CREATE DATABASE IF NOT EXISTS demo_finance")

spark.sql("USE demo_finance")

  

### UC (uncomment if enabled)

### spark.sql("USE CATALOG main")

### spark.sql("CREATE SCHEMA IF NOT EXISTS demo_finance")

### spark.sql("USE SCHEMA demo_finance")

  

### --- Test listing

BUCKET = "<your-bucket-name>"

display(dbutils.fs.ls(f"gs://{BUCKET}/raw/"))
```
  

If you see the three subfolders, you’re ready to run 01_ingest_bronze.py.

---  

## 7) Troubleshooting

  

#### 403 Forbidden / databricks-compute@… lacks storage.objects.get

- Spark is still using the default Databricks compute SA instead of your key.

- Ensure you set all config lines above (google.cloud.* and fs.gs.*, plus ADC env vars). Re-run the cell and retry.

  
#### Catalog 'main' was not found

- Workspace is not on Unity Catalog.

- Use the non-UC path: CREATE DATABASE IF NOT EXISTS demo_finance; USE demo_finance;.

- You can enable UC later if you want SQL Serverless.

  
#### gsutil iam ch errors in PowerShell

Use double quotes and avoid bash line continuations:

gsutil iam ch "serviceAccount:$SA_EMAIL:objectViewer" "gs://$BUCKET"
  

#### Path not found / empty reads

- Verify objects exist at: gs://$BUCKET/raw/{subscriptions,payments,chargebacks}/.

- Use *.csv in Spark CSV reads; watch for extra extensions (e.g., .csv.csv).

---  

## 8) (Optional) Unity Catalog checklist (for SQL Serverless)

  

1. Admin Console → Data → Create Metastore (GCS root path)

  

2. Assign your workspace; make yourself Metastore Admin

  

3. Create Storage Credential (GCP SA) and optional External Location

  

4. Create Catalog (e.g., main) → Schema demo_finance

  

5. Land Bronze/Silver into main.demo_finance.* and run Gold via SQL Serverless (Small, auto-stop 10m)

---  

## 9) Clean up (optional)

  

#### Delete objects and bucket
```
gsutil -m rm -r "gs://$BUCKET/**"

gsutil rb "gs://$BUCKET"

  
#### Revoke key / delete SA

gcloud iam service-accounts keys list --iam-account="$SA_EMAIL"

gcloud iam service-accounts keys delete <KEY_ID> --iam-account="$SA_EMAIL"

gcloud iam service-accounts delete "$SA_EMAIL"


#### Remove Databricks secret

$BodyDel = @{ scope="gcp-secrets"; key="gcs-key-json" } | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$DATABRICKS_HOST/api/2.0/secrets/delete" -Headers $Headers -ContentType "application/json" -Body $BodyDel
```

