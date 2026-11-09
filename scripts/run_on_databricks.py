"""Run the whole demo on a Databricks workspace with one command.

    pip install -r requirements.txt
    python scripts/run_on_databricks.py                       # default profile, default catalog
    python scripts/run_on_databricks.py --schema my_demo --generate

What it does, in order:
1. Creates the schema and a managed Volume named `raw` if they do not exist.
2. Uploads data/raw/{subscriptions,payments,chargebacks}/*.csv to the Volume.
3. Imports the three notebooks to a workspace folder (default /Users/<you>/enterprise-finance-data-demo).
4. Submits one run with three tasks (bronze -> silver -> gold) on serverless compute and waits.
5. Prints each task's status and the Gold KPI tables.
6. With --save-outputs, writes each executed notebook (with its real cell outputs)
   back to databricks/*.ipynb, with the host, workspace id, user name and catalog
   name replaced by placeholders.

Auth comes from the standard Databricks config: DATABRICKS_HOST / DATABRICKS_TOKEN
environment variables, or a profile in ~/.databrickscfg (--profile). Nothing is hard-coded.

Requires a Unity Catalog workspace with serverless jobs compute.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.parse
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service import catalog, jobs
from databricks.sdk.service import workspace as ws

REPO = Path(__file__).resolve().parent.parent
TABLES = ["subscriptions", "payments", "chargebacks"]
NOTEBOOKS = ["01_ingest_bronze", "02_transform_silver", "03_kpis_gold"]
GOLD_ORDER = ["gold_payments_monthly", "gold_chargeback_rate", "gold_revenue_by_plan", "gold_kpi_monthly"]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Run the finance demo pipeline on Databricks.")
    ap.add_argument("--profile", help="profile in ~/.databrickscfg (default: env vars or DEFAULT)")
    ap.add_argument("--catalog", help="Unity Catalog catalog (default: the workspace default catalog)")
    ap.add_argument("--schema", default="demo_finance", help="target schema (default: demo_finance)")
    ap.add_argument("--volume", default="raw", help="managed Volume for the CSVs (default: raw)")
    ap.add_argument("--data-dir", type=Path, default=REPO / "data" / "raw", help="local CSV folder")
    ap.add_argument("--workspace-dir", help="workspace folder for the notebooks "
                    "(default: /Users/<you>/enterprise-finance-data-demo)")
    ap.add_argument("--dedup", choices=["keyed", "none"], default="keyed", help="Silver dedupe strategy")
    ap.add_argument("--generate", action="store_true", help="regenerate the CSVs before uploading")
    ap.add_argument("--timeout-min", type=int, default=30, help="give up waiting after N minutes")
    ap.add_argument("--save-results", type=Path, help="write the Gold results JSON to this file")
    ap.add_argument("--save-outputs", action="store_true",
                    help="write executed notebooks with outputs back to databricks/*.ipynb (scrubbed)")
    return ap.parse_args(argv)


def ensure_schema_and_volume(w: WorkspaceClient, cat: str, schema: str, volume: str):
    try:
        w.schemas.get(f"{cat}.{schema}")
        print(f"schema {cat}.{schema}: exists")
    except NotFound:
        w.schemas.create(name=schema, catalog_name=cat, comment="Finance data demo (bronze/silver/gold)")
        print(f"schema {cat}.{schema}: created")
    try:
        w.volumes.read(f"{cat}.{schema}.{volume}")
        print(f"volume {cat}.{schema}.{volume}: exists")
    except NotFound:
        w.volumes.create(catalog_name=cat, schema_name=schema, name=volume,
                         volume_type=catalog.VolumeType.MANAGED, comment="Raw CSV landing zone")
        print(f"volume {cat}.{schema}.{volume}: created")


def upload_csvs(w: WorkspaceClient, data_dir: Path, vol_root: str):
    for t in TABLES:
        files = sorted((data_dir / t).glob("*.csv"))
        if not files:
            sys.exit(f"No CSVs in {data_dir / t}. Run: python data/generate_data.py")
        for f in files:
            dest = f"{vol_root}/{t}/{f.name}"
            with f.open("rb") as fh:
                w.files.upload(dest, fh, overwrite=True)
            print(f"uploaded {f.relative_to(data_dir)} -> {dest}")


def import_notebooks(w: WorkspaceClient, folder: str):
    w.workspace.mkdirs(folder)
    for nb in NOTEBOOKS:
        src = REPO / "databricks" / f"{nb}.ipynb"
        w.workspace.import_(path=f"{folder}/{nb}", format=ws.ImportFormat.JUPYTER,
                            content=base64.b64encode(src.read_bytes()).decode(), overwrite=True)
        print(f"imported {src.relative_to(REPO)} -> {folder}/{nb}")


def submit_pipeline(w: WorkspaceClient, folder: str, params: dict, timeout_min: int) -> jobs.Run:
    tasks, prev = [], None
    for nb in NOTEBOOKS:
        p = dict(params)
        if nb != "02_transform_silver":
            p.pop("DEDUP_STRATEGY", None)
        if nb != "01_ingest_bronze":
            p.pop("RAW_DIR", None)
        tasks.append(jobs.SubmitTask(
            task_key=nb,
            notebook_task=jobs.NotebookTask(notebook_path=f"{folder}/{nb}", base_parameters=p),
            depends_on=[jobs.TaskDependency(task_key=prev)] if prev else None,
            timeout_seconds=timeout_min * 60,
        ))
        prev = nb
    # No cluster spec on the tasks = serverless jobs compute.
    waiter = w.jobs.submit(run_name="enterprise-finance-data-demo", tasks=tasks,
                           timeout_seconds=timeout_min * 60)
    run_id = waiter.run_id
    print(f"\nsubmitted run {run_id}; waiting (serverless start-up takes about a minute)...")
    started = time.time()
    try:
        run = waiter.result(timeout=timedelta(minutes=timeout_min + 2))
    except Exception as e:  # failed runs raise here; fetch the final state anyway
        print(f"run ended with an error: {type(e).__name__}: {str(e)[:300]}")
        run = w.jobs.get_run(run_id)
    print(f"run {run_id} finished in {time.time() - started:.0f}s: {run.state.result_state.value if run.state.result_state else run.state.life_cycle_state.value}")
    if run.run_page_url:
        print(f"run page: {run.run_page_url}")
    return run


def make_scrubber(w: WorkspaceClient, me: str, cat: str):
    """Replace workspace-specific strings with placeholders before anything is saved."""
    host = re.sub(r"^https?://", "", (w.config.host or "")).rstrip("/")
    pairs = [(host, "<workspace-host>"), (str(w.get_workspace_id()), "<workspace-id>"),
             (me, "<you>"), (cat, "<catalog>")]
    return scrubber_from_pairs([(a, b) for a, b in pairs if a])


def scrubber_from_pairs(pairs):
    """Build scrub(text) from (value, placeholder) pairs.

    Each value is replaced only as a whole token: it must not touch a letter,
    digit or underscore on either side. A plain str.replace would turn
    "domain" into "do<catalog>" for catalog "main", or rewrite part of a
    longer number that happens to contain the workspace id.
    """
    patterns = [(re.compile(r"(?<![A-Za-z0-9_])" + re.escape(a) + r"(?![A-Za-z0-9_])"), b)
                for a, b in pairs]

    def scrub(text: str) -> str:
        for pat, b in patterns:
            text = pat.sub(lambda _m, b=b: b, text)
        return text
    return scrub


def save_executed_notebooks(w: WorkspaceClient, run: jobs.Run, scrub):
    """Export each task run and copy its cell outputs into the repo notebook."""
    by_key = {t.task_key: t for t in run.tasks or []}
    for nb in NOTEBOOKS:
        export = w.jobs.export_run(by_key[nb].run_id, views_to_export=jobs.ViewsToExport.CODE)
        html = export.views[0].content
        m = re.search(r"__DATABRICKS_NOTEBOOK_MODEL = '([^']+)'", html)
        if not m:
            print(f"could not find notebook model in export of {nb}; outputs not saved")
            continue
        model = json.loads(urllib.parse.unquote(base64.b64decode(m.group(1)).decode()))
        commands = sorted(model["commands"], key=lambda c: c["position"])
        path = REPO / "databricks" / f"{nb}.ipynb"
        ipynb = json.loads(path.read_text())
        if len(commands) != len(ipynb["cells"]):
            print(f"{nb}: {len(commands)} commands vs {len(ipynb['cells'])} cells; outputs not saved")
            continue
        count = 0
        for cell, cmd in zip(ipynb["cells"], commands):
            if cell["cell_type"] != "code":
                continue
            count += 1
            cell["execution_count"] = count
            cell["outputs"] = []
            res = cmd.get("results") or {}
            items = res.get("data") if res.get("type") == "listResults" else []
            for item in items or []:
                if item.get("type") == "ansi":
                    cell["outputs"].append({"output_type": "stream", "name": item.get("name", "stdout"),
                                            "text": scrub(item["data"]).splitlines(keepends=True)})
                elif item.get("type") == "mimeBundle":
                    data = {k: scrub(v) for k, v in item["data"].items() if k == "text/plain"}
                    if data and data.get("text/plain") != "DataFrame[]":
                        cell["outputs"].append({"output_type": "execute_result", "execution_count": count,
                                                "data": data, "metadata": {}})
            if cmd.get("error") or cmd.get("errorSummary"):
                cell["outputs"].append({"output_type": "stream", "name": "stderr",
                                        "text": scrub(str(cmd.get("errorSummary") or cmd.get("error")))})
        path.write_text(json.dumps(ipynb, indent=1) + "\n")
        print(f"saved outputs into {path.relative_to(REPO)}")


def print_table(name: str, columns: list, rows: list):
    print(f"\n{name} ({len(rows)} rows)")
    widths = [max(len(str(c)), *(len(str(r.get(c))) for r in rows)) if rows else len(c) for c in columns]
    print("  ".join(str(c).ljust(wd) for c, wd in zip(columns, widths)))
    print("  ".join("-" * wd for wd in widths))
    for r in rows:
        print("  ".join(str(r.get(c)).rjust(wd) if isinstance(r.get(c), (int, float)) else str(r.get(c)).ljust(wd)
                        for c, wd in zip(columns, widths)))


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.generate:
        subprocess.run([sys.executable, str(REPO / "data" / "generate_data.py"), "--out", str(args.data_dir)], check=True)

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    me = w.current_user.me().user_name
    cat = args.catalog or w.metastores.current().default_catalog_name
    if not cat:
        sys.exit("No default catalog on this workspace; pass --catalog.")
    folder = args.workspace_dir or f"/Users/{me}/enterprise-finance-data-demo"
    vol_root = f"/Volumes/{cat}/{args.schema}/{args.volume}"

    ensure_schema_and_volume(w, cat, args.schema, args.volume)
    upload_csvs(w, args.data_dir, vol_root)
    import_notebooks(w, folder)

    params = {"CATALOG": cat, "SCHEMA": args.schema, "RAW_DIR": vol_root, "DEDUP_STRATEGY": args.dedup}
    run = submit_pipeline(w, folder, params, args.timeout_min)

    ok = True
    gold_task = None
    print("\ntask                  status     seconds  task_run_id")
    for t in sorted(run.tasks or [], key=lambda t: t.task_key):
        state = t.state.result_state.value if t.state.result_state else t.state.life_cycle_state.value
        secs = round(((t.end_time or 0) - (t.start_time or 0)) / 1000) if t.end_time else "-"
        print(f"{t.task_key:<22}{state:<11}{secs!s:>7}  {t.run_id}")
        if state != "SUCCESS":
            ok = False
            out = w.jobs.get_run_output(t.run_id)
            if out.error:
                print(f"  error: {out.error[:500]}")
        if t.task_key == "03_kpis_gold":
            gold_task = t

    if not ok or gold_task is None:
        return 1

    out = w.jobs.get_run_output(gold_task.run_id)
    results = json.loads(out.notebook_output.result)
    for name in GOLD_ORDER:
        print_table(name, results[name]["columns"], results[name]["rows"])
    print("\nrevenue totals:", results["totals"])
    if args.save_results:
        args.save_results.write_text(json.dumps({"run_id": run.run_id, "results": results}, indent=1) + "\n")
        print(f"saved results to {args.save_results}")
    if args.save_outputs:
        save_executed_notebooks(w, run, make_scrubber(w, me, cat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
