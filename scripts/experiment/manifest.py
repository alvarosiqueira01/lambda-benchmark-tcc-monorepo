#!/usr/bin/env python3
import argparse, json, platform, subprocess, sys
from pathlib import Path
from common import load_yaml, write_json, sha256_file, git_value, utc_now, is_placeholder

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",default="experiments/harness-config.yaml")
    p.add_argument("--plan",default="experiments/experiment-plan.yaml")
    p.add_argument("--out",default="experiments/metadata/campaign-manifest.json")
    p.add_argument("--run-dir")
    p.add_argument("--run-data",default=None)
    a=p.parse_args(); cfg=load_yaml(a.config); plan=load_yaml(a.plan)
    dataset=Path(cfg["dataset"]["path"])
    manifest={"campaign_id":plan["campaign_id"],"captured_at_utc":utc_now(),
      "account_id":None,"region":cfg["aws"]["region"],"git_commit":git_value(["rev-parse","HEAD"]),
      "git_dirty":bool(git_value(["status","--porcelain"])),
      "dataset":{"path":str(dataset),"version":cfg["dataset"]["version"],
        "sha256":sha256_file(dataset) if dataset.exists() else None,"exists":dataset.exists()},
      "plan":{"path":a.plan,"sha256":sha256_file(a.plan)},
      "config":{"path":a.config,"sha256":sha256_file(a.config)},
      "tools":{"python":sys.version,"platform":platform.platform()},
      "protocol":{"random_seed":plan["random_seed"],"primary_lmax_ms":plan["latency_constraint"]["primary_lmax_ms"],
        "warmup_seconds":plan["warmup_seconds"],"measurement_seconds":plan["measurement_seconds"]}}
    try:
        import boto3
        session=boto3.Session(profile_name=cfg["aws"].get("profile"),region_name=cfg["aws"]["region"])
        manifest["account_id"]=session.client("sts").get_caller_identity()["Account"]
    except Exception as e: manifest["aws_identity_error"]=str(e)
    if a.run_dir:
        run=Path(a.run_dir); run.mkdir(parents=True,exist_ok=True)
        out=run/"manifest.json"
    else: out=Path(a.out)
    write_json(out,manifest); print(out)
if __name__=="__main__": main()
