#!/usr/bin/env python3
import argparse, json, csv
from pathlib import Path
from common import load_yaml, write_json, utc_now

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",default="experiments/experiment-plan.yaml")
    ap.add_argument("--run-dir",required=True)
    ap.add_argument("--expected-requests",type=int,required=True)
    ap.add_argument("--composition",required=True,choices=["monolithic","decomposed"])
    ap.add_argument("--target-verification",required=True)
    ap.add_argument("--concurrent-run-detected",action="store_true")
    a=ap.parse_args(); plan=load_yaml(a.plan); d=Path(a.run_dir); checks=[]; issues=[]
    def check(name,ok,detail):
        checks.append({"name":name,"passed":bool(ok),"detail":detail})
        if not ok: issues.append({"check":name,"detail":detail})
    required=["manifest.json","requests.csv","lambda-invocations.csv","lambda-collection-status.json","cost.json","target-url.json"]
    for f in required: check(f"artifact:{f}",(d/f).exists(),f"{f} exists")
    check("no_concurrent_run",not a.concurrent_run_detected,"no overlapping run")

    target_path=Path(a.target_verification)
    target_info=None
    if target_path.exists():
        target_info=json.loads(target_path.read_text())
    target_ok=bool(target_info and target_info.get("ok"))
    target_composition_ok=bool(target_info and target_info.get("composition")==a.composition)
    check("target_verification_ok",target_ok,f"verify_target ok={target_ok}")
    check("target_composition_match",target_composition_ok,
          f"target-verification composition={target_info.get('composition') if target_info else None} "
          f"vs condition={a.composition}")

    status={}
    if (d/"lambda-collection-status.json").exists():
        status=json.loads((d/"lambda-collection-status.json").read_text())
    check("lambda_collection_complete",status.get("status")=="COMPLETE",str(status.get("status")))

    n_actual=None
    if (d/"requests.csv").exists():
        with open(d/"requests.csv",newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f))
        n_actual=sum(str(r.get("request_sent","")).lower() in ("true","1") for r in rows)
        if n_actual==0: n_actual=len(rows)
    tolerance=float(plan["arrival_schedule"].get("constant_tolerance_fraction",.05))
    lo=int(a.expected_requests*(1-tolerance)); hi=int(a.expected_requests*(1+tolerance))
    check("arrival_count",n_actual is not None and lo<=n_actual<=hi,f"observed={n_actual}, expected={a.expected_requests}, allowed=[{lo},{hi}]")

    system_anomaly=False
    if (d/"requests.csv").exists() and n_actual:
        with open(d/"requests.csv",newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f))
        system_anomaly=any((r.get("status_code","") not in ("200","201","204") or str(r.get("timeout","")).lower()=="true") for r in rows)
    if issues: final="invalid-apparatus"
    elif system_anomaly: final="valid-system-anomaly"
    else: final="valid"
    result={"run_id":d.name,"status":final,"composition":a.composition,"checks":checks,"issues":issues,
      "system_anomaly_observed":system_anomaly,"decision_timestamp_utc":utc_now(),
      "target":target_info,
      "note":"HTTP error/timeout and latency violations are retained as system outcomes; inspect the real API's success status contract before final campaign."}
    write_json(d/"validation.json",result); print(json.dumps(result,indent=2))
if __name__=="__main__": main()