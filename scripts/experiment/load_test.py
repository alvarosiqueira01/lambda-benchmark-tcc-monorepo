#!/usr/bin/env python3
"""Run k6 through SSM, upload the raw output to S3, and download it locally."""
import argparse, json, shlex, time
from pathlib import Path
from common import load_yaml, write_json, utc_now, is_placeholder

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='experiments/harness-config.yaml')
    p.add_argument('--composition', required=True, choices=['monolithic', 'decomposed'])
    p.add_argument('--pattern', required=True, choices=['constant', 'burst', 'sparse_miniburst'])
    p.add_argument('--run-id', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--document-id', required=True)
    a = p.parse_args()
    c = load_yaml(a.config); g = c['generator']; aws = c['aws']
    if is_placeholder(g['instance_id']) or is_placeholder(aws['s3_bucket']):
        raise SystemExit('Configure generator.instance_id and aws.s3_bucket')

    route_cfg = c['api']['routes'][a.composition]
    template = route_cfg['route_path_template']
    if '{id}' not in template:
        raise SystemExit(f"route_path_template para {a.composition} deve conter {{id}}: {template}")
    resolved_path = template.replace('{id}', a.document_id)
    target_url = route_cfg['base_url'].rstrip('/') + resolved_path

    out = Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'target-url.json', {
        'run_id': a.run_id, 'composition': a.composition, 'pattern': a.pattern,
        'target_url': target_url, 'api_id': route_cfg['api_id'],
        'route_path_template': template, 'resolved_path': resolved_path,
        'route_method': c['api']['route_method'],
        'resolved_at_utc': utc_now(),
    })

    import boto3
    session = boto3.Session(profile_name=aws.get('profile'), region_name=aws['region'])
    ssm = session.client('ssm'); s3 = session.client('s3'); bucket = aws['s3_bucket']
    remote = f"{g['remote_output_dir'].rstrip('/')}/{a.run_id}"
    prefix = f"{aws['s3_prefix'].strip('/')}/raw-generator/{a.run_id}"
    envs = {
        'RUN_ID': a.run_id, 'LOAD_PATTERN': a.pattern,
        'TARGET_URL': target_url,
        'OUT_DIR': remote,
        'PREALLOCATED_VUS': str(g.get('pre_allocated_vus', 100)),
        'MAX_VUS': str(g.get('max_vus', 500)),
    }
    exports = '; '.join(f'export {k}={shlex.quote(v)}' for k, v in envs.items())
    cmd = (
        f'set -euo pipefail; mkdir -p {shlex.quote(remote)}; {exports}; '
        f'export T0_EPOCH_MS=$(date -u +%s%3N); echo $T0_EPOCH_MS > {shlex.quote(remote)}/t0_epoch_ms.txt; '
        f'k6 run --out json={shlex.quote(remote)}/k6-raw.json {shlex.quote(g["remote_k6_script"])}; '
        f'date -u +%s%3N > {shlex.quote(remote)}/tend_epoch_ms.txt; '
        f'aws s3 cp {shlex.quote(remote)} s3://{bucket}/{prefix}/ --recursive'
    )
    response = ssm.send_command(
        InstanceIds=[g['instance_id']], DocumentName='AWS-RunShellScript',
        Parameters={'commands': [cmd], 'executionTimeout': [str(g.get('ssm_timeout_seconds', 900))]},
        TimeoutSeconds=int(g.get('ssm_timeout_seconds', 900)),
    )
    command_id = response['Command']['CommandId']
    deadline = time.time() + int(g.get('ssm_timeout_seconds', 900)) + 60
    while time.time() < deadline:
        inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=g['instance_id'])
        if inv['Status'] in ('Success', 'Failed', 'TimedOut', 'Cancelled'):
            break
        time.sleep(3)
    else:
        raise TimeoutError(f'SSM command timeout: {command_id}')
    status = {
        'run_id': a.run_id, 'command_id': command_id, 'ssm_status': inv['Status'],
        'composition': a.composition, 'target_url': target_url,
        'stdout': inv.get('StandardOutputContent', '')[-4000:],
        'stderr': inv.get('StandardErrorContent', '')[-4000:],
        's3_prefix': f's3://{bucket}/{prefix}/',
        'collected_at_utc': utc_now(),
    }
    write_json(out / 'load-test-status.json', status)
    if inv['Status'] != 'Success':
        raise RuntimeError(json.dumps(status))
    objects = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix + '/'):
        objects.extend(page.get('Contents', []))
    if not objects:
        raise RuntimeError(f'No generator artifacts in s3://{bucket}/{prefix}/')
    for obj in objects:
        rel = obj['Key'][len(prefix) + 1:]
        dest = out / rel; dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, obj['Key'], str(dest))
    t0 = int((out / 't0_epoch_ms.txt').read_text().strip())
    tend = int((out / 'tend_epoch_ms.txt').read_text().strip())
    write_json(out / 'generator-timing.json', {'run_id': a.run_id, 't0_epoch_ms': t0, 'tend_epoch_ms': tend})
    print(json.dumps({**status, 't0_epoch_ms': t0, 'tend_epoch_ms': tend, 'downloaded_objects': len(objects)}, indent=2))

if __name__ == '__main__':
    main()