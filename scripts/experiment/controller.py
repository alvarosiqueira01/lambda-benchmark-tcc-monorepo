#!/usr/bin/env python3
"""Sequential campaign controller. Requires completed config and generator artifact-return path."""
import argparse, json, subprocess, sys, time, os, shutil
from pathlib import Path
from common import load_yaml, csv_read, new_run_id, write_json, utc_now, sha256_file, is_placeholder

def call(args):
    print("+", " ".join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),check=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--plan",default="experiments/experiment-plan.yaml")
    p.add_argument("--config",default="experiments/harness-config.yaml")
    p.add_argument("--condition-file",required=True)
    p.add_argument("--pricing",default=None)
    p.add_argument("--limit",type=int)
    p.add_argument("--start-index",type=int,default=0)
    a=p.parse_args(); plan=load_yaml(a.plan); cfg=load_yaml(a.config)
    rows=csv_read(a.condition_file)[a.start_index:]
    if a.limit: rows=rows[:a.limit]
    if not rows: raise SystemExit("No conditions selected")
    lock=Path("experiments/.campaign.lock")
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY); os.write(fd,str(os.getpid()).encode()); os.close(fd)
    except FileExistsError: raise SystemExit("Campaign lock exists; verify no controller/run is active before removing it.")
    try:
        for row in rows:
            block=int(row.get("block_id",1)); rep=int(row.get("replication",block))
            run_id=new_run_id(row["condition_id"],block,rep); run_dir=Path("experiments/runs")/run_id; run_dir.mkdir(parents=True)
            manifest={"run_id":run_id,"condition":row,"created_at_utc":utc_now(),"status":"PREPARING",
              "plan_sha256":sha256_file(a.plan),"config_sha256":sha256_file(a.config)}
            write_json(run_dir/"manifest.json",manifest)
            try:
                reset=run_dir/"cold-reset.json"
                call([sys.executable,"scripts/experiment/deploy.py","--config",a.config,"--validate-only"])
                call([sys.executable,"scripts/experiment/cold_reset.py","--config",a.config,
                  "--composition",row["composition"],"--architecture",row["architecture"],
                  "--memory-mb",row["memory_mb"],"--run-id",run_id,"--out",reset])
                reset_data=json.loads(reset.read_text())
                functions=[x["function_name"] for x in reset_data["functions"]]

                # Blocking check: integration URI must be alias-qualified and target the
                # expected function/alias for this composition, and alias must be at the
                # version just published by cold_reset.py. No application invoke here.
                target_verification=run_dir/"target-verification.json"
                call([sys.executable,"scripts/experiment/verify_target.py","--config",a.config,
                  "--composition",row["composition"],"--cold-reset",reset,
                  "--out",target_verification])
                target_data=json.loads(target_verification.read_text())
                if not target_data.get("ok"):
                    raise RuntimeError(f"verify_target failed for {row['composition']}: {target_data}")
                manifest["api_target"]={
                    "composition":target_data["composition"],
                    "route_key":target_data["route_key"],
                    "integration_id":target_data["integration_id"],
                    "integration_uri":target_data["integration_uri"],
                    "expected_function_name":target_data["expected_function_name"],
                    "expected_alias":target_data["expected_alias"],
                    "expected_version_from_cold_reset":target_data["expected_version_from_cold_reset"],
                }
                write_json(run_dir/"manifest.json",manifest)

                load_test=run_dir/"load-test-status.json"
                call([sys.executable,"scripts/experiment/load_test.py","--config",a.config,
                  "--composition",row["composition"],
                  "--pattern",row["load_pattern"],"--run-id",run_id,"--output-dir",run_dir,
                  "--document-id",load_yaml(a.config)["dataset"].get("document_id","") or get_document_id(cfg)])
                times=json.loads((run_dir/"generator-timing.json").read_text())
                start_ms=int(times["t0_epoch_ms"]); end_ms=start_ms+int(plan["total_duration_seconds"]*1000)
                call([sys.executable,"scripts/experiment/export_k6_requests.py","--raw",run_dir/"k6-raw.json",
                  "--t0-epoch-ms",start_ms,"--warmup-seconds",plan["warmup_seconds"],"--out",run_dir/"requests.csv"])
                collect_start=start_ms-120000; collect_end=end_ms+120000
                collect_args=[sys.executable,"scripts/experiment/collect_lambda.py","--config",a.config,
                  "--run-id",run_id,"--start-ms",start_ms,"--end-ms",end_ms,
                  "--output-dir",run_dir,"--versions-json",reset]
                for fn in functions: collect_args.extend(["--function",fn])
                call(collect_args)
                call([sys.executable,"scripts/experiment/collect_cloudwatch.py","--config",a.config,
                  "--run-id",run_id,"--start-ms",collect_start,"--end-ms",collect_end,
                  "--output-dir",run_dir,*sum([["--function",f] for f in functions],[])])
                if not a.pricing: raise RuntimeError("Specify --pricing snapshot before cost calculation.")
                call([sys.executable,"scripts/experiment/collect_cost.py","--config",a.config,"--pricing",a.pricing,
                  "--requests-csv",run_dir/"requests.csv","--lambda-csv",run_dir/"lambda-invocations.csv",
                  "--composition",row["composition"],"--architecture",row["architecture"],"--t0-epoch-ms",start_ms,"--output",run_dir/"cost.json"])
                call([sys.executable,"scripts/experiment/validate_run.py","--plan",a.plan,"--run-dir",run_dir,
                  "--expected-requests",int(plan["load_patterns"][row["load_pattern"]]["expected_requests_total"]),
                  "--composition",row["composition"],
                  "--target-verification",target_verification])
                import boto3
                s3=boto3.Session(profile_name=cfg["aws"].get("profile"),region_name=cfg["aws"]["region"]).client("s3")
                bucket=cfg["aws"]["s3_bucket"]; prefix=f'{cfg["aws"]["s3_prefix"].rstrip("/")}/runs/{run_id}'
                for file in run_dir.rglob("*"):
                    if file.is_file(): s3.upload_file(str(file),bucket,f'{prefix}/{file.relative_to(run_dir).as_posix()}')
                manifest.update({"status":"COLLECTED","functions":reset_data["functions"],"finished_at_utc":utc_now(),"t0_epoch_ms":start_ms,"end_epoch_ms":end_ms})
                write_json(run_dir/"manifest.json",manifest)
            except Exception as e:
                manifest.update({"status":"FAILED_OR_PARTIAL","error":str(e),"finished_at_utc":utc_now()})
                write_json(run_dir/"manifest.json",manifest)
                print(f"Run {run_id} failed; artifacts preserved: {e}",file=sys.stderr)
                raise
    finally:
        try: lock.unlink()
        except FileNotFoundError: pass

def get_document_id(cfg):
    import json
    p=Path(cfg["dataset"]["path"])
    if not p.exists(): raise FileNotFoundError(f"Dataset missing: {p}")
    data=json.loads(p.read_text(encoding="utf-8"))
    if not data: raise ValueError("Dataset is empty")
    first=data[0]
    if "id" not in first: raise ValueError("First dataset item has no 'id'")
    return str(first["id"])

if __name__=="__main__": main()