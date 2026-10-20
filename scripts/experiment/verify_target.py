#!/usr/bin/env python3
"""Control-plane verification of the API Gateway target for a given composition.

Does not invoke the application. Fails if the integration URI is unqualified (would
bypass the cold-reset alias) or points at the wrong function/alias/version.
"""
import argparse, json, re
from pathlib import Path
import boto3
from common import load_yaml, write_json, utc_now

INTEGRATION_ARN_RE = re.compile(
    r"functions/(arn:aws:lambda:(?P<region>[^:]+):(?P<account>\d+):"
    r"function:(?P<name>[^:/]+)(?::(?P<qual>[^/]+))?)/invocations"
)

def find_route_integration(apigw, api_id, route_method, route_path_template):
    route_key = f"{route_method} {route_path_template}"
    routes = apigw.get_routes(ApiId=api_id).get("Items", [])
    matches = [r for r in routes if r.get("RouteKey") == route_key]
    if len(matches) != 1:
        raise SystemExit(f"Esperava exatamente 1 rota {route_key!r} em {api_id}, encontrei {len(matches)}")
    target = matches[0]["Target"]
    if not target.startswith("integrations/"):
        raise SystemExit(f"Target inesperado para {route_key!r}: {target}")
    integration_id = target.split("/", 1)[1]
    return integration_id, apigw.get_integration(ApiId=api_id, IntegrationId=integration_id)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--composition", required=True, choices=["monolithic", "decomposed"])
    p.add_argument("--cold-reset", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()

    cfg = load_yaml(a.config)
    reset = json.loads(Path(a.cold_reset).read_text())
    route_cfg = cfg["api"]["routes"][a.composition]
    expected_key = route_cfg.get("expected_function_key",
                                 "monolithic" if a.composition == "monolithic" else "orchestrator")
    expected_fn = cfg["functions"][expected_key]["name"]
    expected_alias = cfg["functions"][expected_key].get("alias", "benchmark")

    session = boto3.Session(profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"])
    apigw = session.client("apigatewayv2"); lam = session.client("lambda")

    route_method = cfg["api"]["route_method"]
    route_template = route_cfg["route_path_template"]
    api_id = route_cfg["api_id"]
    integration_id, integration = find_route_integration(apigw, api_id, route_method, route_template)
    uri = integration.get("IntegrationUri", "")

    m = INTEGRATION_ARN_RE.search(uri)
    checks = {}
    if not m:
        checks["uri_has_lambda_arn"] = False
        actual_fn = actual_qual = None
    else:
        checks["uri_has_lambda_arn"] = True
        actual_fn = m.group("name"); actual_qual = m.group("qual")
        checks["uri_is_alias_qualified"] = actual_qual is not None
        checks["targets_expected_function"] = actual_fn == expected_fn
        checks["targets_expected_alias"] = actual_qual == expected_alias

    alias = lam.get_alias(FunctionName=expected_fn, Name=expected_alias)
    alias_version = alias["FunctionVersion"]
    expected_version = next(
        (f["version"] for f in reset["functions"] if f["function_name"] == expected_fn), None)
    checks["alias_points_to_reset_version"] = expected_version == alias_version

    result = {
        "created_at_utc": utc_now(), "composition": a.composition,
        "api_id": api_id, "route_method": route_method,
        "route_key": f"{route_method} {route_template}",
        "integration_id": integration_id, "integration_uri": uri,
        "expected_function_name": expected_fn, "expected_alias": expected_alias,
        "expected_version_from_cold_reset": expected_version,
        "actual_function_name_in_uri": actual_fn, "actual_qualifier_in_uri": actual_qual,
        "alias_function_version": alias_version,
        "checks": checks, "ok": all(checks.values()) and checks.get("uri_has_lambda_arn", False),
    }
    write_json(Path(a.out), result)
    if not result["ok"]:
        raise SystemExit("verify_target falhou: " + json.dumps(result, indent=2))

if __name__ == "__main__":
    main()