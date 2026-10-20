#!/usr/bin/env python3
"""Collect Lambda platform START/REPORT records by time window and published version."""
import argparse, json, re, time
from datetime import datetime
from pathlib import Path
from common import load_yaml, write_json, csv_write, utc_now
START_RE=re.compile(r'START RequestId: ([0-9a-fA-F-]+) Version: (\S+)')
REPORT_RE=re.compile(r'REPORT RequestId: ([0-9a-fA-F-]+).*?Duration: ([0-9.]+) ms.*?Billed Duration: ([0-9]+) ms.*?Memory Size: ([0-9]+) MB.*?Max Memory Used: ([0-9]+) MB(?:.*?Init Duration: ([0-9.]+) ms)?')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--config',default='experiments/harness-config.yaml')
    p.add_argument('--run-id',required=True); p.add_argument('--start-ms',required=True,type=int); p.add_argument('--end-ms',required=True,type=int)
    p.add_argument('--output-dir',required=True); p.add_argument('--function',action='append',required=True); p.add_argument('--versions-json',required=True)
    a=p.parse_args(); cfg=load_yaml(a.config)
    import boto3
    session=boto3.Session(profile_name=cfg['aws'].get('profile'),region_name=cfg['aws']['region']); logs=session.client('logs')
    vdoc=json.loads(Path(a.versions_json).read_text()); expected={x['function_name']:{x['version']} for x in vdoc.get('functions',[])}
    rows=[]; issues=[]; start_ms=a.start_ms-120000; end_ms=a.end_ms+120000
    for fn in a.function:
        group=f'/aws/lambda/{fn}'
        try:
            events=[]; token=None
            while True:
                kw={'logGroupName':group,'startTime':start_ms,'endTime':end_ms}
                if token: kw['nextToken']=token
                resp=logs.filter_log_events(**kw); events.extend(resp.get('events',[]))
                nt=resp.get('nextToken')
                if not nt or nt==token: break
                token=nt
        except logs.exceptions.ResourceNotFoundException:
            issues.append(f'log group missing: {group}'); continue
        starts={}; reports={}
        for e in events:
            msg=e.get('message','')
            sm=START_RE.search(msg)
            if sm: starts[sm.group(1)]={'version':sm.group(2),'timestamp_ms':int(e['timestamp'])}
            rm=REPORT_RE.search(msg)
            if rm: reports[rm.group(1)]={'duration_ms':float(rm.group(2)),'billed_duration_ms':int(rm.group(3)),
                'memory_mb':int(rm.group(4)),'max_memory_used_mb':int(rm.group(5)),
                'init_duration_ms':float(rm.group(6)) if rm.group(6) else None,'report_timestamp_ms':int(e['timestamp'])}
        for rid,st in starts.items():
            if st['version'] not in expected.get(fn,{st['version']}): continue
            if not (a.start_ms <= st['timestamp_ms'] < a.end_ms): continue
            rep=reports.get(rid,{})
            rows.append({'run_id':a.run_id,'function_name':fn,'version':st['version'],'request_id':rid,
              'start_timestamp_ms':st['timestamp_ms'],'report_timestamp_ms':rep.get('report_timestamp_ms'),
              'duration_ms':rep.get('duration_ms'),'billed_duration_ms':rep.get('billed_duration_ms'),
              'memory_mb':rep.get('memory_mb'),'max_memory_used_mb':rep.get('max_memory_used_mb'),
              'init_duration_ms':rep.get('init_duration_ms'),'cold_start':rep.get('init_duration_ms') is not None,
              'report_found':bool(rep),'architecture':next((x['architecture'] for x in vdoc.get('functions',[]) if x['function_name']==fn),'')})
    missing=sum(not r['report_found'] for r in rows)
    if missing: issues.append(f'{missing} START records have no paired REPORT')
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    csv_write(out/'lambda-invocations.csv',rows)
    write_json(out/'lambda-collection-status.json',{'run_id':a.run_id,'collected_at_utc':utc_now(),'records':len(rows),
      'reports_missing':missing,'issues':issues,'status':'COMPLETE' if rows and not issues else 'INCOMPLETE',
      'selection':'time window plus published Lambda version; platform START/REPORT lines do not contain application run_id'})
    print(f'Collected {len(rows)} invocations; missing REPORT={missing}; issues={issues}')
if __name__=='__main__': main()
