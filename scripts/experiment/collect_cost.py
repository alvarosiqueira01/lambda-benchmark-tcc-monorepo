#!/usr/bin/env python3
"""Analytical Lambda/API Gateway/DynamoDB cost; emits full-run and measurement-window costs."""
import argparse, json
from pathlib import Path
import pandas as pd
from common import load_yaml, write_json, utc_now

def lambda_cost(rows, prices, architecture):
    duration=0.0; n=0
    rate=prices['lambda_gb_second_usd_arm64'] if architecture=='arm64' else prices['lambda_gb_second_usd_x86_64']
    for r in rows:
        if str(r.get('report_found','')).lower() not in ('true','1') or r.get('billed_duration_ms') in ('',None): continue
        mem=float(r['memory_mb'])/1024
        duration += float(r['billed_duration_ms'])/1000*mem*rate; n+=1
    request=n*prices['lambda_request_usd_per_million']/1e6
    return {'lambda_duration_usd':duration,'lambda_request_usd':request,'lambda_total_usd':duration+request,'lambda_invocations_counted':n}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--config',default='experiments/harness-config.yaml'); p.add_argument('--pricing',required=True)
    p.add_argument('--requests-csv',required=True); p.add_argument('--lambda-csv',required=True)
    p.add_argument('--composition',required=True,choices=['monolithic','decomposed']); p.add_argument('--architecture',required=True,choices=['x86_64','arm64'])
    p.add_argument('--t0-epoch-ms',type=int,default=None); p.add_argument('--output',required=True); a=p.parse_args()
    cfg=load_yaml(a.config); prices=json.loads(Path(a.pricing).read_text()); req=pd.read_csv(a.requests_csv).fillna(''); lam=pd.read_csv(a.lambda_csv).fillna('')
    def window_cost(requests, invocations, label):
        n=int(requests['request_sent'].astype(str).str.lower().isin(['true','1']).sum()) if 'request_sent' in requests else len(requests)
        lc=lambda_cost(invocations.to_dict('records'),prices,a.architecture)
        api=n*prices['http_api_usd_per_million_requests_first_tier']/1e6
        c=cfg['cost']; rru=float(c.get('ddb_rru_per_external_request',0))*n; wru=float(c.get('ddb_wru_per_external_request',0))*n
        ddb=rru*prices['dynamodb_standard_usd_per_million_rru']/1e6+wru*prices['dynamodb_standard_usd_per_million_wru']/1e6
        total=lc['lambda_total_usd']+api+ddb
        return {'window':label,'external_requests_counted':n,**lc,'api_gateway_estimated_usd':api,'dynamodb_estimated_usd':ddb,
          'app_total_estimated_usd':total,'c_req_lambda_usd':lc['lambda_total_usd']/n if n else None,
          'c_req_app_usd':total/n if n else None}
    full=window_cost(req,lam,'full_run')
    measurement_req=req[req['phase']=='measurement'] if 'phase' in req else req
    measurement_lam=lam
    if a.t0_epoch_ms is not None and 'start_timestamp_ms' in lam:
        times=pd.to_numeric(lam['start_timestamp_ms'],errors='coerce')
        measurement_lam=lam[(times>=a.t0_epoch_ms+120000)&(times<a.t0_epoch_ms+240000)]
    measurement=window_cost(measurement_req,measurement_lam,'measurement')
    result={'created_at_utc':utc_now(),'composition':a.composition,'architecture':a.architecture,'full_run':full,'measurement':measurement,
      'pricing_snapshot':a.pricing,'dynamodb_estimation_assumptions':{'rru_per_external_request':cfg['cost'].get('ddb_rru_per_external_request'),
      'wru_per_external_request':cfg['cost'].get('ddb_wru_per_external_request'),'warning':'DynamoDB costs are estimates until unit consumption and item size/consistency are verified.'}}
    write_json(a.output,result); print(json.dumps(result,indent=2))
if __name__=='__main__': main()
