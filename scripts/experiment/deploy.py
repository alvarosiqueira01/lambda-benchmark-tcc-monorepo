#!/usr/bin/env python3
"""Validate existing infrastructure; optionally perform initial SAM deploy."""
import argparse, json, re, subprocess, sys
from pathlib import Path
from common import load_yaml, is_placeholder, write_json, utc_now

INTEGRATION_ARN_RE = re.compile(
    r"functions/(arn:aws:lambda:(?P<region>[^:]+):(?P<account>\d+):"
    r"function:(?P<name>[^:/]+)(?::(?P<qual>[^/]+))?)/invocations"
)

def run(cmd, check=True):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=True)

def validate_route(apigw, api_id, route_method, route_path_template, expected_fn_name, expected_alias):
    route_key = f"{route_method} {route_path_template}"
    routes = apigw.get_routes(ApiId=api_id).get("Items", [])
    matches = [r for r in routes if r.get("RouteKey") == route_key]
    if len(matches) != 1:
        raise SystemExit(f"Rota {route_key!r} em {api_id}: esperava 1, encontrou {len(matches)}")
    target = matches[0]["Target"]
    if not target.startswith("integrations/"):
        raise SystemExit(f"Target inesperado para {route_key!r}: {target}")
    integration_id = target.split("/", 1)[1]
    integ = apigw.get_integration(ApiId=api_id, IntegrationId=integration_id)
    uri = integ.get("IntegrationUri", "")
    m = INTEGRATION_ARN_RE.search(uri)
    if not m:
        raise SystemExit(f"IntegrationUri de {route_key!r} não é ARN Lambda reconhecível: {uri}")
    if m.group("qual") is None:
        raise SystemExit(
            f"IntegrationUri de {route_key!r} sem qualificador de alias (uri={uri}). "
            f"O cold reset via alias será ignorado. Corrija o template SAM e redeploy."
        )
    if m.group("name") != expected_fn_name:
        raise SystemExit(f"IntegrationUri de {route_key!r} aponta para {m.group('name')!r}, esperado {expected_fn_name!r}")
    if m.group("qual") != expected_alias:
        raise SystemExit(f"IntegrationUri de {route_key!r} qualifica por {m.group('qual')!r}, esperado {expected_alias!r}")
    return {
        "api_id": api_id, "route_key": route_key, "integration_id": integration_id,
        "integration_uri": uri, "function_in_uri": m.group("name"),
        "qualifier_in_uri": m.group("qual"),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--template", default="template.yaml")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--deploy", action="store_true")
    ap.add_argument("--guided", action="store_true")
    a = ap.parse_args(); cfg = load_yaml(a.config)

    required = [
        ("aws.stack_name", cfg["aws"].get("stack_name")),
        ("functions.monolithic.name", cfg["functions"]["monolithic"].get("name")),
        ("functions.orchestrator.name", cfg["functions"]["orchestrator"].get("name")),
    ]
    for comp in ("monolithic", "decomposed"):
        rc = cfg["api"]["routes"].get(comp) or {}
        required.extend([
            (f"api.routes.{comp}.api_id", rc.get("api_id")),
            (f"api.routes.{comp}.base_url", rc.get("base_url")),
            (f"api.routes.{comp}.route_path_template", rc.get("route_path_template")),
        ])
    missing = [k for k, v in required if is_placeholder(v)]
    if missing:
        raise SystemExit("Preencha harness-config.yaml: " + ", ".join(missing))

    if not a.validate_only and not Path(a.template).exists():
        raise SystemExit(f"Template {a.template} não encontrado. Use --validate-only ou informe --template correto.")
    if Path(a.template).exists():
        run(["sam", "validate", "--lint", "--template-file", a.template])
        if a.deploy:
            run(["sam", "build", "--template-file", a.template])
            cmd = ["sam", "deploy", "--stack-name", cfg["aws"]["stack_name"],
                   "--region", cfg["aws"]["region"], "--no-confirm-changeset"]
            if a.guided:
                cmd.append("--guided")
            run(cmd)

    import boto3
    session = boto3.Session(profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"])
    lam = session.client("lambda"); apigw = session.client("apigatewayv2")

    fn_specs = [cfg["functions"]["monolithic"], cfg["functions"]["orchestrator"], *cfg["functions"].get("workers", [])]
    checks = []
    for spec in fn_specs:
        if is_placeholder(spec["name"]):
            continue
        fn = lam.get_function(FunctionName=spec["name"])
        aliases = lam.list_aliases(FunctionName=spec["name"]).get("Aliases", [])
        target_alias = spec.get("alias", "benchmark")
        alias = next((x for x in aliases if x["Name"] == target_alias), None)
        if alias is None:
            raise SystemExit(
                f"Alias {target_alias!r} não existe em {spec['name']}. "
                f"Faça deploy do template com AWS::Lambda::Alias antes de rodar o piloto."
            )
        checks.append({
            "function": spec["name"], "state": fn["Configuration"].get("State"),
            "alias": target_alias, "alias_version": alias["FunctionVersion"],
            "qualified_arn": alias["AliasArn"],
            "code_sha256": fn["Configuration"].get("CodeSha256"),
        })

    method = cfg["api"]["route_method"]
    route_results = []
    for comp in ("monolithic", "decomposed"):
        rc = cfg["api"]["routes"][comp]
        api_id = rc["api_id"]
        expected_key = rc.get("expected_function_key",
                              "monolithic" if comp == "monolithic" else "orchestrator")
        fn_name = cfg["functions"][expected_key]["name"]
        alias_name = cfg["functions"][expected_key].get("alias", "benchmark")

        api = apigw.get_api(ApiId=api_id)
        endpoint = api["ApiEndpoint"].rstrip("/")
        stage = cfg["api"].get("stage", "$default")
        expected_base = endpoint if stage in (None, "$default") else f"{endpoint}/{stage}"
        configured_base = rc["base_url"].rstrip("/")
        if configured_base != expected_base:
            raise SystemExit(
                f"base_url de {comp} ({configured_base}) não corresponde a {api_id} com stage {stage!r} "
                f"(esperado: {expected_base}). api_id e base_url de APIs diferentes."
            )
        route_results.append(validate_route(
            apigw, api_id, method, rc["route_path_template"], fn_name, alias_name))

    result = {
        "checked_at_utc": utc_now(),
        "stack_name": cfg["aws"]["stack_name"],
        "functions": checks,
        "routes": route_results,
        "note": "Ambas as rotas devem referenciar o alias benchmark; verify_target.py re-checa por run.",
    }
    Path("experiments/metadata").mkdir(parents=True, exist_ok=True)
    write_json("experiments/metadata/deploy-validation.json", result)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()