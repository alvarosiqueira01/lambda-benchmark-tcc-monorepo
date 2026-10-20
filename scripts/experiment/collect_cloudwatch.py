#!/usr/bin/env python3
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from common import load_yaml, write_json, utc_now

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",default="experiments/harness-config.yaml")
    ap.add_argument("--run-id",required=True); ap.add_argument("--start-ms",required=True,type=int)
    ap.add_argument("--end-ms",required=True,type=int); ap.add_argument("--output-dir",required=True)
    ap.add_argument("--function",action="append",default=[])
    a=ap.parse_args(); cfg=load_yaml(a.config)
    import boto3
    session=boto3.Session(profile_name=cfg["aws"].get("profile"),region_name=cfg["aws"]["region"])
    cw=session.client("cloudwatch"); rows=[]; raw=[]
    start=datetime.fromtimestamp(a.start_ms/1000,timezone.utc); end=datetime.fromtimestamp(a.end_ms/1000,timezone.utc)
    metrics=["Invocations","Errors","Throttles","Duration","ConcurrentExecutions"]
    for fn in a.function:
        for metric in metrics:
            resp=cw.get_metric_statistics(Namespace="AWS/Lambda",MetricName=metric,
                Dimensions=[{"Name":"FunctionName","Value":fn}],StartTime=start,EndTime=end,
                Period=60,Statistics=["Sum","Average","Maximum"])
            raw.append({"namespace":"AWS/Lambda","function":fn,"metric":metric,"response":resp})
            for dp in resp.get("Datapoints",[]):
                rows.append({"run_id":a.run_id,"namespace":"AWS/Lambda","function_name":fn,"metric":metric,
                  "timestamp_utc":dp["Timestamp"].isoformat(),"unit":dp.get("Unit"),
                  "sum":dp.get("Sum"),"average":dp.get("Average"),"maximum":dp.get("Maximum")})
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    write_json(out/"cloudwatch-raw.json",raw)
    from common import csv_write
    csv_write(out/"cloudwatch-metrics.csv",rows)
    write_json(out/"cloudwatch-collection-status.json",{"run_id":a.run_id,"collected_at_utc":utc_now(),
      "records":len(rows),"functions":a.function,"status":"COMPLETE" if rows else "EMPTY",
      "note":"CloudWatch metrics are supplementary and may be delayed/coarse; k6 request samples and Lambda REPORT logs remain primary."})
if __name__=="__main__": main()
