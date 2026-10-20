#!/usr/bin/env python3
"""Export custom per-request k6 Trend samples to CSV."""
import argparse, csv, json
from pathlib import Path
from datetime import datetime, timezone

def main():
    p=argparse.ArgumentParser(); p.add_argument('--raw',required=True); p.add_argument('--t0-epoch-ms',type=int,required=True); p.add_argument('--warmup-seconds',type=int,default=120); p.add_argument('--out',required=True); a=p.parse_args()
    rows=[]
    with open(a.raw,encoding='utf-8') as f:
        for line in f:
            try: obj=json.loads(line)
            except json.JSONDecodeError: continue
            if obj.get('metric')!='bench_request_latency' or obj.get('type')!='Point': continue
            d=obj.get('data',{}); tags=d.get('tags') or {}; stamp=d.get('time')
            if not stamp: continue
            dt=datetime.fromisoformat(stamp.replace('Z','+00:00')); sampled_ms=int(dt.timestamp()*1000)
            latency=float(d.get('value') or 0); ms=sampled_ms-int(latency); offset=ms-a.t0_epoch_ms
            phase=tags.get('phase') or ('warmup' if offset < a.warmup_seconds*1000 else ('measurement' if offset < 240000 else 'outside_run'))
            rows.append({'run_id':tags.get('run_id',''),'request_sequence':len(rows)+1,'started_at_utc':dt.astimezone(timezone.utc).isoformat(),
              'start_epoch_ms':ms,'offset_ms':offset,'phase':phase,'latency_ms':d.get('value'),
              'status_code':tags.get('status_code',''),'timeout':tags.get('timeout','false'),'request_sent':'true',
              'scenario':tags.get('scenario','')})
    if not rows: raise SystemExit('No bench_request_latency samples; check k6 version/output format and custom Trend.')
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True)
    fields=list(rows[0].keys())
    with open(out,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f'{len(rows)} request samples -> {out}')
if __name__=='__main__': main()
