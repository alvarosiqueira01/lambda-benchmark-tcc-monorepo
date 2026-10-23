#!/usr/bin/env bash
set -Eeuo pipefail

# =============================================================================
# Document API Benchmark - Dataset Size Probe (pre-protocol calibration)
# =============================================================================
# Measures warm Duration of the MONOLITH for 3 reference configurations
# and checks the dataset against the acceptance criteria.
#
# Matrix (all x86_64 to isolate memory effect):
#   128/x86_64 (low), 512/x86_64 (mid), 2048/x86_64 (high)
# =============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

STACK_NAME="${STACK_NAME:-document-api-benchmark}"
EXPECTED_RUNTIME="${EXPECTED_RUNTIME:-python3.14}"
EXPECTED_TIMEOUT="${EXPECTED_TIMEOUT:-10}"
DATASET_VERSION="${DATASET_VERSION:-v1}"
DATASET_FILE="${DATASET_FILE:-dataset/generated/${DATASET_VERSION}/documents.json}"
RESULTS_DIR="${RESULTS_DIR:-probe-results}"
HTTP_MAX_TIME="${HTTP_MAX_TIME:-60}"
RUN_SEED="${RUN_SEED:-true}"

# Probe protocol
WARMUP_INVOCATIONS="${WARMUP_INVOCATIONS:-3}"
MEASUREMENT_INVOCATIONS="${MEASUREMENT_INVOCATIONS:-20}"
INTER_INVOCATION_SLEEP="${INTER_INVOCATION_SLEEP:-0.5}"
POST_WARMUP_BUFFER_S="${POST_WARMUP_BUFFER_S:-10}"
POST_MEASUREMENT_WAIT="${POST_MEASUREMENT_WAIT:-45}"
LOG_WAIT_ATTEMPTS="${LOG_WAIT_ATTEMPTS:-15}"
LOG_WAIT_SECONDS="${LOG_WAIT_SECONDS:-8}"
LOG_WINDOW_BEFORE_MS="${LOG_WINDOW_BEFORE_MS:-2000}"
LOG_WINDOW_AFTER_MS="${LOG_WINDOW_AFTER_MS:-5000}"

# Acceptance criteria
CRITERION_P50_512_MIN="${CRITERION_P50_512_MIN:-50}"
CRITERION_P50_512_MAX="${CRITERION_P50_512_MAX:-300}"
CRITERION_RATIO_128_2048_MIN="${CRITERION_RATIO_128_2048_MIN:-2.0}"
CRITERION_MEM_128_MAX_MB="${CRITERION_MEM_128_MAX_MB:-110}"
CRITERION_COLD_RATE_MAX="${CRITERION_COLD_RATE_MAX:-0.10}"
CRITERION_INIT_FRACTION_MAX="${CRITERION_INIT_FRACTION_MAX:-0.20}"

# Probe matrix: label|memory|architecture  (all x86_64)
PROBES=(
  "low|128|x86_64"
  "mid|512|x86_64"
  "high|2048|x86_64"
)

if [[ -n "${PYTHON_RUNNER:-}" ]]; then
  # shellcheck disable=SC2206
  PYTHON_CMD=(${PYTHON_RUNNER})
elif command -v uv >/dev/null 2>&1 && [[ -n "${VIRTUAL_ENV:-}" ]]; then
  PYTHON_CMD=(uv run --active python)
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
else
  echo "ERROR: Python não encontrado" >&2
  exit 1
fi

mkdir -p "$RESULTS_DIR"
SUMMARY_FILE="$RESULTS_DIR/summary.tsv"
printf 'label\tmemory\tarchitecture\tstatus\tdetail\n' > "$SUMMARY_FILE"

TMP_ROOT="$(mktemp -d)"
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

log()  { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; return 1; }

for cmd in aws sam curl jq; do
  command -v "$cmd" >/dev/null 2>&1 || { fail "comando não encontrado: $cmd"; exit 1; }
done

[[ -f "$DATASET_FILE" ]] || { fail "dataset não encontrado: $DATASET_FILE"; exit 1; }

MONO_URL=""
MONOLITH_TABLE=""
MONOLITH_FUNCTION=""
DOC_ID=""
MEASUREMENT_START_MS=0
MEASUREMENT_END_MS=0

get_stack_outputs() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs' --output json
}

get_output_exact() {
  local key="$1"
  get_stack_outputs \
    | jq -r --arg key "$key" '.[] | select(.OutputKey == $key) | .OutputValue' \
    | head -n 1
}

resolve_function_by_logical_id() {
  aws cloudformation list-stack-resources \
    --stack-name "$STACK_NAME" \
    --query "StackResourceSummaries[?LogicalResourceId=='$1'].PhysicalResourceId | [0]" \
    --output text
}

resolve_stack_values() {
  MONO_URL="$(get_output_exact MonolithApiUrl)"
  MONOLITH_TABLE="$(get_output_exact MonolithTableName)"
  MONOLITH_FUNCTION="$(resolve_function_by_logical_id MonolithFunction)"

  [[ -n "$MONO_URL" && "$MONO_URL" != "null" ]]         || { fail "MonolithApiUrl não encontrado"; return 1; }
  [[ -n "$MONOLITH_TABLE" && "$MONOLITH_TABLE" != "null" ]] || { fail "MonolithTableName não encontrado"; return 1; }
  [[ -n "$MONOLITH_FUNCTION" && "$MONOLITH_FUNCTION" != "None" ]] || { fail "MonolithFunction não encontrado"; return 1; }

  echo "MONO_URL=$MONO_URL"
  echo "MONOLITH_TABLE=$MONOLITH_TABLE"
  echo "MONOLITH_FUNCTION=$MONOLITH_FUNCTION"
}

assert_monolith_config() {
  local arch="$1" mem="$2"
  local cfg="$TMP_ROOT/mono.config.json"
  aws lambda get-function-configuration \
    --function-name "$MONOLITH_FUNCTION" \
    --query '{Runtime:Runtime,Architectures:Architectures,MemorySize:MemorySize,Timeout:Timeout}' \
    --output json > "$cfg"
  cat "$cfg"
  jq -e \
    --arg runtime "$EXPECTED_RUNTIME" \
    --arg arch "$arch" \
    --argjson memory "$mem" \
    --argjson timeout "$EXPECTED_TIMEOUT" \
    '(.Runtime == $runtime) and (.Architectures | length == 1) and (.Architectures[0] == $arch) and (.MemorySize == $memory) and (.Timeout == $timeout)' \
    "$cfg" >/dev/null || { fail "config inválida do monolith (arch=$arch mem=$mem)"; return 1; }
}

seed_one_table() {
  local table="$1" label="$2"
  log "Seed $label: table=$table dataset=$DATASET_FILE"
  "${PYTHON_CMD[@]}" "$SCRIPT_DIR/seed_dynamodb.py" \
    --table "$table" --dataset "$DATASET_FILE"
}

get_dataset_document() {
  DOC_ID="$(jq -r '.[0].id' "$DATASET_FILE")"
  [[ -n "$DOC_ID" && "$DOC_ID" != "null" ]] || { fail "não foi possível extrair o primeiro id do dataset"; return 1; }
  echo "Dataset document ID: $DOC_ID"
}

http_get() {
  curl -sS --max-time "$HTTP_MAX_TIME" -o /dev/null -w '%{http_code}' "$1"
}

now_ms() { echo $(( $(date +%s) * 1000 )); }

run_measurement() {
  local url="$1"
  local status i

  log "Warm-up: $WARMUP_INVOCATIONS invocations"
  for i in $(seq 1 "$WARMUP_INVOCATIONS"); do
    status="$(http_get "$url")"
    if [[ "$status" != "200" ]]; then
      fail "warm-up invocação $i retornou HTTP $status"
      return 1
    fi
    sleep 0.2
  done

  log "Post-warm-up buffer: ${POST_WARMUP_BUFFER_S}s"
  sleep "$POST_WARMUP_BUFFER_S"

  MEASUREMENT_START_MS="$(now_ms)"
  log "Measurement: $MEASUREMENT_INVOCATIONS invocations (start_ms=$MEASUREMENT_START_MS)"

  for i in $(seq 1 "$MEASUREMENT_INVOCATIONS"); do
    status="$(http_get "$url")"
    if [[ "$status" != "200" ]]; then
      fail "measurement $i retornou HTTP $status"
      return 1
    fi
    sleep "$INTER_INVOCATION_SLEEP"
  done

  MEASUREMENT_END_MS="$(now_ms)"
  log "Measurement end (end_ms=$MEASUREMENT_END_MS, elapsed=$((MEASUREMENT_END_MS - MEASUREMENT_START_MS))ms)"
  return 0
}

collect_and_report() {
  local label="$1" mem="$2" arch="$3"
  local raw_file="$TMP_ROOT/${label}-report.json"
  local stats_file="$RESULTS_DIR/${label}_${mem}_${arch}_stats.json"
  local attempt=0 count=0
  local log_start_ms=$((MEASUREMENT_START_MS - LOG_WINDOW_BEFORE_MS))
  local log_end_ms=$((MEASUREMENT_END_MS + LOG_WINDOW_AFTER_MS))

  log "Waiting ${POST_MEASUREMENT_WAIT}s for log ingestion"
  sleep "$POST_MEASUREMENT_WAIT"

  log "Collecting CloudWatch REPORT events for $MONOLITH_FUNCTION"
  log "  window: ${log_start_ms} .. ${log_end_ms}"

  while (( attempt < LOG_WAIT_ATTEMPTS )); do
    aws logs filter-log-events \
      --log-group-name "/aws/lambda/$MONOLITH_FUNCTION" \
      --start-time "$log_start_ms" \
      --end-time "$log_end_ms" \
      --filter-pattern 'REPORT' \
      --output json > "$raw_file"

    count=$(jq -r '.events | length' "$raw_file")
    (( count >= MEASUREMENT_INVOCATIONS )) && break

    log "  attempt $((attempt+1))/$LOG_WAIT_ATTEMPTS: got $count / $MEASUREMENT_INVOCATIONS; waiting ${LOG_WAIT_SECONDS}s"
    sleep "$LOG_WAIT_SECONDS"
    attempt=$((attempt+1))
  done

  if (( count < MEASUREMENT_INVOCATIONS )); then
    fail "esperava >= $MEASUREMENT_INVOCATIONS eventos REPORT, obteve $count"
    return 1
  fi

  log "Parsing $count events"
  "${PYTHON_CMD[@]}" "$SCRIPT_DIR/probe_stats.py" "$raw_file" > "$stats_file"
  cat "$stats_file"
}

run_probe() {
  local label="$1" mem="$2" arch="$3"
  local url

  echo
  echo "================================================================"
  echo "Probe: label=$label memory=${mem}MB architecture=$arch"
  echo "================================================================"

  log "sam deploy (mem=$mem arch=$arch)"
  sam deploy \
    --parameter-overrides \
      LambdaMemoryMb="$mem" \
      LambdaArchitecture="$arch" \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset

  resolve_stack_values
  assert_monolith_config "$arch" "$mem"

  if [[ "$RUN_SEED" == "true" ]]; then
    log "Seed DynamoDB ($DATASET_VERSION)"
    seed_one_table "$MONOLITH_TABLE" "monolith"
  fi

  get_dataset_document

  url="${MONO_URL%/}/documents/$DOC_ID/analysis"
  [[ "$url" =~ ^https?://[^/]+/.+ ]] || { fail "URL inválida: [$url]"; return 1; }
  echo "Probe URL: $url"

  run_measurement "$url"
  collect_and_report "$label" "$mem" "$arch"
}

main() {
  echo "==============================================================="
  echo "Document API Benchmark - Dataset Size Probe"
  echo "==============================================================="
  echo "Project root : $PROJECT_ROOT"
  echo "Stack        : $STACK_NAME"
  echo "Dataset      : $DATASET_FILE"
  echo "Results dir  : $RESULTS_DIR"
  echo "Warm-up      : $WARMUP_INVOCATIONS"
  echo "Measurement  : $MEASUREMENT_INVOCATIONS"
  echo "Python runner: ${PYTHON_CMD[*]}"
  echo "Probes       : ${PROBES[*]}"
  echo

  log "sam build (once)"
  sam build

  local entry label mem arch rc failures=0
  for entry in "${PROBES[@]}"; do
    IFS='|' read -r label mem arch <<< "$entry"
    set +e
    (
      set -Eeuo pipefail
      run_probe "$label" "$mem" "$arch"
    ) 2>&1 | tee "$RESULTS_DIR/${label}_${mem}_${arch}.log"
    rc=${PIPESTATUS[0]}
    set -e

    if [[ "$rc" -eq 0 ]]; then
      printf '%s\t%s\t%s\tPASS\t-\n' "$label" "$mem" "$arch" >> "$SUMMARY_FILE"
    else
      failures=$((failures + 1))
      printf '%s\t%s\t%s\tFAIL\tsee log\n' "$label" "$mem" "$arch" >> "$SUMMARY_FILE"
      echo "FAIL: label=$label mem=$mem arch=$arch" >&2
    fi
  done

  if (( failures > 0 )); then
    echo "ERROR: $failures probe(s) falharam durante a execução." >&2
    return 2
  fi

  echo
  echo "==============================================================="
  echo "Aggregating and evaluating criteria"
  echo "==============================================================="

  "${PYTHON_CMD[@]}" "$SCRIPT_DIR/probe_report.py" \
    --results-dir "$RESULTS_DIR" \
    --report "$RESULTS_DIR/report.md" \
    --verdict "$RESULTS_DIR/verdict.json" \
    --criterion-p50-512-min "$CRITERION_P50_512_MIN" \
    --criterion-p50-512-max "$CRITERION_P50_512_MAX" \
    --criterion-ratio-128-2048-min "$CRITERION_RATIO_128_2048_MIN" \
    --criterion-mem-128-max-mb "$CRITERION_MEM_128_MAX_MB" \
    --criterion-cold-rate-max "$CRITERION_COLD_RATE_MAX" \
    --criterion-init-fraction-max "$CRITERION_INIT_FRACTION_MAX"

  cat "$RESULTS_DIR/report.md"
  return $?
}

main "$@"
