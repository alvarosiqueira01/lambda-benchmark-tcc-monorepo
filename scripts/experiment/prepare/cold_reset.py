#!/usr/bin/env python3
"""Apply factor settings, publish fresh versions and move benchmark aliases.

Architecture is a property of UpdateFunctionCode, not UpdateFunctionConfiguration.
When the requested architecture differs from the deployed one, the current
deployment package is downloaded and re-uploaded with the new architecture.
Every function update is awaited before the next step, and a run fails if
PublishVersion does not create a strictly newer version.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import is_placeholder, load_yaml, utc_now, write_json


def wait_update(client, name, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        c = client.get_function_configuration(FunctionName=name)
        status = c.get("LastUpdateStatus")
        if status == "Successful" and c.get("State") == "Active":
            return c
        if status == "Failed":
            raise RuntimeError(f"Lambda update failed for {name}: {c}")
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for {name}")


def latest_published_version(client, name):
    versions = []
    for page in client.get_paginator("list_versions_by_function").paginate(FunctionName=name):
        for item in page.get("Versions", []):
            version = str(item.get("Version", ""))
            if version.isdigit():
                versions.append(int(version))
    return max(versions) if versions else 0


def wait_alias(client, name, alias_name, version, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get_alias(FunctionName=name, Name=alias_name)
        if last.get("FunctionVersion") == str(version):
            return last
        time.sleep(2)
    raise TimeoutError(
        f"Alias {name}:{alias_name} did not reach version {version}; last={last}"
    )


def download_current_package(client, name):
    info = client.get_function(FunctionName=name)
    location = info.get("Code", {}).get("Location")
    if not location:
        raise RuntimeError(
            f"Cannot download deployment package for {name}: no Code.Location. "
            "Container images are not supported by this reset path."
        )
    with urllib.request.urlopen(location, timeout=120) as response:
        data = response.read()
    if len(data) > 50 * 1024 * 1024:
        raise RuntimeError(
            f"Deployment package for {name} is larger than the 50 MB direct-upload limit "
            "for UpdateFunctionCode."
        )
    return data


def update_architecture(client, name, arch, current_arch):
    if current_arch == arch:
        return "none"
    package = download_current_package(client, name)
    client.update_function_code(
        FunctionName=name,
        ZipFile=package,
        Architectures=[arch],
        Publish=False,
    )
    wait_update(client, name)
    return "UpdateFunctionCode(ZipFile+Architectures)"


def stack_snapshot(session, stack_name):
    if is_placeholder(stack_name):
        return {"available": False, "reason": "stack_name not configured"}
    try:
        stack = session.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
        return {
            "available": True,
            "stack_name": stack.get("StackName"),
            "stack_id": stack.get("StackId"),
            "stack_status": stack.get("StackStatus"),
            "last_updated_time": str(stack.get("LastUpdatedTime") or ""),
            "creation_time": str(stack.get("CreationTime") or ""),
        }
    except Exception as exc:  # noqa: BLE001 - the snapshot is diagnostic
        return {"available": False, "error": str(exc)}


def reset_function(client, spec, arch, memory, run_id):
    name = spec["name"]
    alias_name = spec.get("alias", "benchmark")
    if is_placeholder(name):
        raise ValueError(f"Function name not configured: {name}")

    before = client.get_function_configuration(FunctionName=name)
    current_arch = (before.get("Architectures") or ["x86_64"])[0]
    before_view = {
        "memory_mb": before.get("MemorySize"),
        "architecture": current_arch,
        "description": before.get("Description"),
        "code_sha256": before.get("CodeSha256"),
        "last_modified": before.get("LastModified"),
    }

    arch_method = update_architecture(client, name, arch, current_arch)
    client.update_function_configuration(
        FunctionName=name,
        MemorySize=int(memory),
        Description=f"benchmark-run:{run_id}",
    )
    wait_update(client, name)

    previous_version = latest_published_version(client, name)
    version = client.publish_version(FunctionName=name, Description=f"benchmark-run:{run_id}")
    v = int(version["Version"])
    if v <= previous_version:
        raise RuntimeError(
            f"PublishVersion for {name} did not create a new version: "
            f"previous={previous_version}, published={v}"
        )
    expected_memory = int(memory)
    # PublishVersion responses have varied across SDK/API versions; always read
    # the authoritative immutable version configuration before asserting.
    published = client.get_function_configuration(FunctionName=name, Qualifier=str(v))
    if published.get("State") != "Active":
        raise RuntimeError(
            f"Published version {name}:{v} is not Active: {published.get('State')!r}"
        )
    if int(published.get("MemorySize", -1)) != expected_memory:
        raise RuntimeError(
            f"Published version {name}:{v} has MemorySize={published.get('MemorySize')}, "
            f"expected {expected_memory}"
        )
    published_arch = (published.get("Architectures") or [None])[0]
    if published_arch != arch:
        raise RuntimeError(
            f"Published version {name}:{v} has architecture={published_arch}, expected {arch}"
        )

    client.update_alias(FunctionName=name, Name=alias_name, FunctionVersion=str(v))
    alias = wait_alias(client, name, alias_name, v)

    after_view = {
        "memory_mb": published.get("MemorySize"),
        "architecture": (published.get("Architectures") or [None])[0],
        "description": published.get("Description"),
        "code_sha256": published.get("CodeSha256"),
        "last_modified": published.get("LastModified"),
        "version": str(v),
        "alias_version": alias["FunctionVersion"],
    }
    return {
        "function_name": name,
        "alias": alias_name,
        "version": str(v),
        "previous_version": previous_version,
        "version_is_new": v > previous_version,
        "qualified_arn": alias["AliasArn"],
        "memory_mb": published["MemorySize"],
        "architecture": (published.get("Architectures") or [None])[0],
        "timeout_seconds": published["Timeout"],
        "code_sha256": published["CodeSha256"],
        "runtime": published.get("Runtime"),
        "runtime_version_arn": published.get("RuntimeVersionConfig", {}).get("RuntimeVersionArn"),
        "last_modified": published.get("LastModified"),
        "description": published.get("Description"),
        "architecture_update_method": arch_method,
        "drift": {
            "managed_outside_cloudformation": True,
            "fields": ["MemorySize", "Architectures", "Description", "Alias"],
            "before": before_view,
            "after": after_view,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--composition", required=True, choices=["monolithic", "decomposed"])
    ap.add_argument("--architecture", required=True, choices=["x86_64", "arm64"])
    ap.add_argument("--memory-mb", required=True, type=int)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = load_yaml(a.config)

    import boto3

    session = boto3.Session(
        profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"]
    )
    client = session.client("lambda")
    specs = (
        [cfg["functions"]["monolithic"]]
        if a.composition == "monolithic"
        else [cfg["functions"]["orchestrator"], *cfg["functions"]["workers"]]
    )
    stack_before = stack_snapshot(session, cfg["aws"].get("stack_name"))
    records = [reset_function(client, spec, a.architecture, a.memory_mb, a.run_id) for spec in specs]
    stack_after = stack_snapshot(session, cfg["aws"].get("stack_name"))
    result = {
        "run_id": a.run_id,
        "reset_completed_at_utc": utc_now(),
        "composition": a.composition,
        "architecture": a.architecture,
        "memory_mb": a.memory_mb,
        "functions": records,
        "application_invocations_performed": False,
        "stack": {
            "before": stack_before,
            "after": stack_after,
            "expected_drift": True,
            "note": (
                "cold_reset changes Lambda configuration and aliases outside "
                "CloudFormation by design; a later sam deploy would overwrite the "
                "benchmark configuration."
            ),
        },
    }
    write_json(a.out, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
