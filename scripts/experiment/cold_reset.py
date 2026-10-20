#!/usr/bin/env python3
"""Apply factor settings, publish fresh versions and move benchmark aliases. No application invocations."""
import argparse, time, json
from common import load_yaml, write_json, utc_now, is_placeholder

def wait_update(client,name,timeout=300):
    deadline=time.time()+timeout
    while time.time()<deadline:
        c=client.get_function_configuration(FunctionName=name)
        if c.get("LastUpdateStatus")=="Successful" and c.get("State") in ("Active",None): return c
        if c.get("LastUpdateStatus")=="Failed": raise RuntimeError(f"Lambda update failed for {name}: {c}")
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for {name}")

def reset_function(client,spec,arch,memory,run_id):
    name=spec["name"]; alias_name=spec.get("alias","benchmark")
    if is_placeholder(name): raise ValueError(f"Function name not configured: {name}")
    before=client.get_function_configuration(FunctionName=name)
    client.update_function_configuration(FunctionName=name,MemorySize=int(memory),Architectures=[arch],
        Description=f"benchmark-run:{run_id}")
    c=wait_update(client,name)
    version=client.publish_version(FunctionName=name,Description=f"benchmark-run:{run_id}")
    v=version["Version"]
    if v=="$LATEST": raise RuntimeError("PublishVersion returned $LATEST")
    client.update_alias(FunctionName=name,Name=alias_name,FunctionVersion=v)
    alias=client.get_alias(FunctionName=name,Name=alias_name)
    if alias["FunctionVersion"]!=v: raise RuntimeError(f"Alias {name}:{alias_name} did not move to {v}")
    published=client.get_function_configuration(FunctionName=name,Qualifier=v)
    return {"function_name":name,"alias":alias_name,"version":v,"qualified_arn":alias["AliasArn"],
      "memory_mb":published["MemorySize"],"architecture":published["Architectures"][0],
      "timeout_seconds":published["Timeout"],"code_sha256":published["CodeSha256"],
      "runtime":published.get("Runtime"),"runtime_version_arn":published.get("RuntimeVersionConfig",{}).get("RuntimeVersionArn"),
      "last_modified":published.get("LastModified"),"description":published.get("Description")}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",default="experiments/harness-config.yaml")
    ap.add_argument("--composition",required=True,choices=["monolithic","decomposed"])
    ap.add_argument("--architecture",required=True,choices=["x86_64","arm64"])
    ap.add_argument("--memory-mb",required=True,type=int)
    ap.add_argument("--run-id",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args(); cfg=load_yaml(a.config)
    import boto3
    session=boto3.Session(profile_name=cfg["aws"].get("profile"),region_name=cfg["aws"]["region"])
    client=session.client("lambda")
    specs=[cfg["functions"]["monolithic"]] if a.composition=="monolithic" else [cfg["functions"]["orchestrator"],*cfg["functions"]["workers"]]
    records=[]
    for spec in specs: records.append(reset_function(client,spec,a.architecture,a.memory_mb,a.run_id))
    result={"run_id":a.run_id,"reset_completed_at_utc":utc_now(),"composition":a.composition,
      "architecture":a.architecture,"memory_mb":a.memory_mb,"functions":records,
      "application_invocations_performed":False}
    write_json(a.out,result); print(json.dumps(result,indent=2))
if __name__=="__main__": main()
