# Optional: land the raw CSVs in Google Cloud Storage

> **Not exercised in the tested run.** The tested path reads the CSVs from a Unity Catalog Volume (see the README). This page describes how you could point the same notebooks at a GCS bucket instead. The steps below are a guide, not something this repo verifies.

The notebooks only care about one thing: `RAW_DIR` must be a folder Spark can read, laid out like this:

```
<RAW_DIR>/subscriptions/subscriptions.csv
<RAW_DIR>/payments/payments.csv
<RAW_DIR>/chargebacks/chargebacks.csv
```

So moving to GCS means (1) copy the files to a bucket and (2) give Databricks read access to that bucket.

## 1. Create a bucket and upload the files

Use the same region as your Databricks workspace.

```bash
PROJECT="<your-project-id>"
REGION="us-central1"
BUCKET="<your-bucket-name>"

gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" --uniform-bucket-level-access
gcloud storage cp -r data/raw/* "gs://$BUCKET/raw/"
gcloud storage ls "gs://$BUCKET/raw/"
```

## 2. Give Databricks read access

**Recommended: Unity Catalog external location (no keys).** This is the option that works with serverless compute.

1. In Catalog Explorer, create a **storage credential** of type "GCP service account". Databricks shows a service account email.
2. In GCP, grant that service account `roles/storage.objectViewer` on the bucket:
   ```bash
   gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
     --member="serviceAccount:<service-account-email-from-databricks>" \
     --role="roles/storage.objectViewer"
   ```
3. Create an **external location** for `gs://<your-bucket-name>/raw` that uses the credential, and grant yourself `READ FILES` on it.

**Legacy: a service account key in Databricks Secrets.** Only works on classic clusters, not serverless. You create a key, store it in a secret scope, and set the `fs.gs.auth.service.account.json.keyfile` Spark config on the cluster. Long-lived keys are a security risk, so prefer the external location.

## 3. Run the notebooks against the bucket

Run `01_ingest_bronze` with the widget `RAW_DIR = gs://<your-bucket-name>/raw`, then `02` and `03` as usual. Or run all three as a job with that parameter. Nothing else changes: Bronze, Silver and Gold are the same.

## 4. Clean up

```bash
gcloud storage rm -r "gs://$BUCKET"
```

Then delete the external location and storage credential in Catalog Explorer.
