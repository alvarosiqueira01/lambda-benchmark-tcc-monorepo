#!/usr/bin/env bash
# Resume block 4 of the frozen campaign (tcc-2026-11) after the 2026-11-11
# host crash. Idempotent: it recomputes the conditions without a usable run
# every time it is executed.
#
# Usage:
#   bash experiments/runs/_resume/resume_block_04.sh
#   bash experiments/runs/_resume/resume_block_04.sh --check-only
#
# The script never deletes run artifacts. It preserves the WIP by stashing it
# for the duration of the controller run and restoring it on exit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO"

BLOCK=4
PHASE=campaign
PLAN="experiments/experiment-plan.yaml"
CONFIG="experiments/harness-config.yaml"
PRICING="experiments/pricing/pricing-2026-10-22.json"
CONDITIONS="experiments/metadata/plan/conditions_block_04.csv"
RUNS_DIR="experiments/runs/campaign"
RESULTS="experiments/metadata/campaign-controller-results.json"
LOCK="experiments/.campaign.lock"
EXPECTED_COMMIT="6d3c9f162c6cc4772f3fd499ae28ad3e2106e474"
STASH_MARKER="tcc-resume-block-04"
MAX_CLOCK_OFFSET_MS=500

CHECK_ONLY=0
if [ "${1:-}" = "--check-only" ]; then
  CHECK_ONLY=1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$SCRIPT_DIR/resume-block-04-$STAMP.log"
MISSING_CSV="$SCRIPT_DIR/conditions_block_04_missing.csv"
REPORT="$SCRIPT_DIR/resume-report-$STAMP.json"

STASHED=0
CLOCK_OFFSET_MS=""
MISSING_BEFORE=""
MISSING_AFTER=""
CTRL_RC=""

exec > >(tee -a "$LOG") 2>&1

say() { printf '%s\n' "$*"; }
die() { printf 'ERRO: %s\n' "$*" >&2; exit 1; }

restore_wip() {
  local rc=$?
  if [ "$STASHED" = "1" ]; then
    say "Restaurando o WIP (git stash pop)..."
    git stash pop || say "AVISO: git stash pop falhou; veja 'git stash list' e restaure manualmente."
  fi
  exit "$rc"
}
trap restore_wip EXIT

say "== Retomada do bloco $BLOCK — $(date -u +%Y-%m-%dT%H:%M:%SZ) =="
say "HEAD: $(git rev-parse HEAD)"
[ "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT" ] \
  || die "HEAD diferente do commit congelado ($EXPECTED_COMMIT). Não commite nem altere os insumos congelados antes de retomar."

say "-- 1. relógio (NTP)"
[ "$(timedatectl show -p NTPSynchronized --value)" = "yes" ] \
  || die "NTP não sincronizado. Rode os comandos de NTP do RESUME-BLOCK-04.md antes de retomar."

CLOCK_OFFSET_MS="$(python3 - <<'PY'
import socket
import struct
import time

def offset_ms(host, timeout=5.0):
    packet = b"\x1b" + 47 * b"\x00"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sent = time.time()
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(48)
        received = time.time()
        seconds, fraction = struct.unpack("!12I", data)[10:12]
        server = seconds - 2208988800 + fraction / 2**32
        return (sent + (received - sent) / 2 - server) * 1000.0
    finally:
        sock.close()

for host in ("a.st1.ntp.br", "pool.ntp.org"):
    try:
        print(f"{offset_ms(host):.1f}")
        break
    except OSError:
        continue
else:
    raise SystemExit(1)
PY
)" || die "Não foi possível medir o offset NTP."
say "offset medido: ${CLOCK_OFFSET_MS} ms (limite ${MAX_CLOCK_OFFSET_MS} ms)"
python3 - "$CLOCK_OFFSET_MS" "$MAX_CLOCK_OFFSET_MS" <<'PY' \
  || die "Offset do relógio acima do limite; sincronize o NTP antes de retomar."
import sys
raise SystemExit(0 if abs(float(sys.argv[1])) <= float(sys.argv[2]) else 1)
PY

say "-- 2. credenciais AWS"
python3 - <<'PY' || die "Credenciais AWS inválidas/expiradas (renove o SSO)."
import sys
sys.path.insert(0, "scripts/experiment")
import boto3
from common import load_yaml
cfg = load_yaml("experiments/harness-config.yaml")
session = boto3.Session(
    profile_name=cfg["aws"].get("profile"),
    region_name=cfg["aws"]["region"],
)
print("identidade:", session.client("sts").get_caller_identity()["Arn"])
PY

say "-- 3. concorrência no gerador"
mkdir -p /tmp/opencode
CONC="/tmp/opencode/concurrency-resume-$STAMP.json"
python3 scripts/experiment/prepare/check_concurrency.py \
  --config "$CONFIG" --run-id "resume-check-$STAMP" --out "$CONC" >/dev/null
python3 - "$CONC" <<'PY' || die "Há run ativo no gerador; não iniciar outro."
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print("concurrency ok:", data.get("ok"), "| matches:", data.get("matches"))
raise SystemExit(0 if data.get("ok") is True else 1)
PY

say "-- 4. lock"
if [ -e "$LOCK" ]; then
  PID="$(tr -dc '0-9' < "$LOCK" || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    die "Lock ativo (PID $PID): existe um controller rodando."
  fi
  if [ "$CHECK_ONLY" = "1" ]; then
    say "lock órfão seria removido (PID ${PID:-?} morto)"
  else
    rm -f "$LOCK"
    say "lock órfão removido (PID ${PID:-?} morto)"
  fi
else
  say "sem lock"
fi

say "-- 5. condições pendentes"
python3 "$SCRIPT_DIR/missing_conditions.py" \
  --conditions "$CONDITIONS" --runs-dir "$RUNS_DIR" --block "$BLOCK" --out "$MISSING_CSV"
MISSING_BEFORE=$(( $(wc -l < "$MISSING_CSV") - 1 ))
say "pendentes antes: $MISSING_BEFORE"

if git stash list | grep -q "$STASH_MARKER"; then
  die "Existe um stash anterior '$STASH_MARKER'. Rode 'git stash pop' antes de retomar."
fi

if [ "$MISSING_BEFORE" -eq 0 ]; then
  say "Nada a fazer: todas as 60 condições do bloco $BLOCK já têm run utilizável."
  exit 0
fi

if [ "$CHECK_ONLY" = "1" ]; then
  say "-- check-only: nada será executado nem alterado"
  python3 - <<'PY'
import subprocess
import sys
sys.path.insert(0, "scripts/experiment")
from planning.manifest import is_runtime_artifact

output = subprocess.run(
    ["git", "status", "--porcelain", "--untracked-files=all"],
    capture_output=True, text=True, check=True,
).stdout
paths = []
for line in output.splitlines():
    if not line:
        continue
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    if not is_runtime_artifact(path) and not path.startswith("experiments/archive/"):
        paths.append(path)
print("WIP que seria stashed:", paths if paths else "nenhum")
PY
  say "OK: pré-requisitos atendidos."
  exit 0
fi

say "-- 6. stash automático do WIP"
mapfile -t STASH_PATHS < <(python3 - <<'PY'
import subprocess
import sys
sys.path.insert(0, "scripts/experiment")
from planning.manifest import is_runtime_artifact

output = subprocess.run(
    ["git", "status", "--porcelain", "--untracked-files=all"],
    capture_output=True, text=True, check=True,
).stdout
for line in output.splitlines():
    if not line:
        continue
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    if not is_runtime_artifact(path) and not path.startswith("experiments/archive/"):
        print(path)
PY
)
if [ "${#STASH_PATHS[@]}" -gt 0 ]; then
  say "guardando: ${STASH_PATHS[*]}"
  git stash push -u -m "$STASH_MARKER $STAMP" -- "${STASH_PATHS[@]}"
  STASHED=1
else
  say "árvore limpa; sem stash"
fi
python3 - <<'PY' || die "Árvore ainda suja após o stash; o controller recusaria iniciar."
import sys
sys.path.insert(0, "scripts/experiment")
from planning.manifest import git_dirty
raise SystemExit(0 if not git_dirty() else 1)
PY

say "-- 7. backup dos resultados anteriores"
if [ -f "$RESULTS" ]; then
  cp "$RESULTS" "$SCRIPT_DIR/controller-results-backup-$STAMP.json"
  say "backup: $SCRIPT_DIR/controller-results-backup-$STAMP.json"
fi

say "-- 8. finalização de runs parciais do bloco $BLOCK"
python3 - <<'PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path("scripts/experiment").resolve()))
import boto3
from common import load_yaml
from controller import finalize_failure

cfg = load_yaml("experiments/harness-config.yaml")
session = boto3.Session(
    profile_name=cfg["aws"].get("profile"),
    region_name=cfg["aws"]["region"],
)
for run_dir in sorted(Path("experiments/runs/campaign").glob("*-b04-*")):
    if not run_dir.is_dir() or (run_dir / "validation.json").exists():
        continue
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"AVISO: {run_dir.name} sem manifest.json; pulando")
        continue
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = manifest.get("condition") or {}
    print(f"finalizando run parcial {run_dir.name}")
    finalize_failure(
        run_dir, manifest, session, cfg, run_dir.name, row,
        "controller_crash",
        RuntimeError("controller host crashed/rebooted before load; no traffic generated"),
        "campaign",
    )
PY

say "-- 9. executando o controller (pode levar ~3 h)"
set +e
python3 scripts/experiment/controller.py \
  --plan "$PLAN" \
  --config "$CONFIG" \
  --pricing "$PRICING" \
  --phase "$PHASE" \
  --condition-file "$MISSING_CSV"
CTRL_RC=$?
set -e
say "controller rc=$CTRL_RC"

say "-- 10. verificação de cobertura"
POST_CSV="$SCRIPT_DIR/conditions_block_04_missing_after.csv"
python3 "$SCRIPT_DIR/missing_conditions.py" \
  --conditions "$CONDITIONS" --runs-dir "$RUNS_DIR" --block "$BLOCK" --out "$POST_CSV"
MISSING_AFTER=$(( $(wc -l < "$POST_CSV") - 1 ))

REPORT="$REPORT" STAMP="$STAMP" CLOCK_OFFSET_MS="$CLOCK_OFFSET_MS" \
MISSING_BEFORE="$MISSING_BEFORE" MISSING_AFTER="$MISSING_AFTER" \
CTRL_RC="$CTRL_RC" STASHED="$STASHED" LOG="$LOG" \
STASH_PATHS_JOINED="${STASH_PATHS[*]:-}" \
python3 - <<'PY'
import json
import os
report = {
    "block": 4,
    "stamp_utc": os.environ["STAMP"],
    "clock_offset_ms": float(os.environ["CLOCK_OFFSET_MS"]),
    "head": "6d3c9f162c6cc4772f3fd499ae28ad3e2106e474",
    "missing_before": int(os.environ["MISSING_BEFORE"]),
    "missing_after": int(os.environ["MISSING_AFTER"]),
    "controller_rc": int(os.environ["CTRL_RC"]),
    "stash_used": os.environ["STASHED"] == "1",
    "stashed_paths": os.environ["STASH_PATHS_JOINED"].split(),
    "log": os.environ["LOG"],
}
with open(os.environ["REPORT"], "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
print("relatório:", os.environ["REPORT"])
PY

if [ "$MISSING_AFTER" -eq 0 ]; then
  say "Bloco $BLOCK completo: 60/60 condições com run utilizável."
  say "Próximos passos: arquivar o bloco 4 e seguir para o bloco 5."
  exit 0
fi

say "Ainda faltam $MISSING_AFTER condições no bloco $BLOCK. Veja $POST_CSV"
say "Rode este script novamente para retentar apenas o que falta."
exit 2
