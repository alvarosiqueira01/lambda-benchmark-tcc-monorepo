#!/usr/bin/env bash
set -Eeuo pipefail

# Document API Benchmark - Smoke Test Matrix
# Matrix: x86_64/arm64 x 128/256/512/1024/2048 MB
# Functional/deployment smoke tests only; this is not the performance benchmark.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

STACK_NAME="${STACK_NAME:-document-api-benchmark}"
EXPECTED_RUNTIME="${EXPECTED_RUNTIME:-python3.14}"
EXPECTED_TIMEOUT="${EXPECTED_TIMEOUT:-10}"
DATASET_VERSION="${DATASET_VERSION:-v1}"
DATASET_FILE="${DATASET_FILE:-dataset/generated/${DATASET_VERSION}/documents.json}"
RESULTS_DIR="${RESULTS_DIR:-smoke-results}"
HTTP_MAX_TIME="${HTTP_MAX_TIME:-60}"
RUN_SEED="${RUN_SEED:-true}"
VERIFY_DECOMPOSED_LOGS="${VERIFY_DECOMPOSED_LOGS:-true}"
LOG_WAIT_ATTEMPTS="${LOG_WAIT_ATTEMPTS:-10}"
LOG_WAIT_SECONDS="${LOG_WAIT_SECONDS:-10}"

# Prefer the active virtual environment. This avoids the .venv/.venv-1 warning
# produced when uv is asked to use the project environment implicitly.
if [[ -n "${PYTHON_RUNNER:-}" ]]; then
  # shellcheck disable=SC2206
  PYTHON_CMD=(${PYTHON_RUNNER})
elif command -v uv >/dev/null 2>&1 && [[ -n "${VIRTUAL_ENV:-}" ]]; then
  PYTHON_CMD=(uv run --active python)
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=(python)
else
  echo "ERROR: Python não encontrado" >&2
  exit 1
fi

MEMORIES=(128 256 512 1024 2048)
ARCHITECTURES=(x86_64 arm64)

MONO_URL_KEY="${MONO_URL_KEY:-MonolithApiUrl}"
DECOMP_URL_KEY="${DECOMP_URL_KEY:-DecomposedApiUrl}"
MONO_TABLE_KEY="${MONO_TABLE_KEY:-MonolithTableName}"
DECOMP_TABLE_KEY="${DECOMP_TABLE_KEY:-DecomposedTableName}"

SEED_SCRIPT="${SEED_SCRIPT:-scripts/seed_dynamodb.py}"

mkdir -p "$RESULTS_DIR"
SUMMARY_FILE="$RESULTS_DIR/summary.tsv"
printf 'architecture\tmemory\tstatus\tdetail\n' > "$SUMMARY_FILE"

TMP_ROOT="$(mktemp -d)"
CURRENT_ARCH=""
CURRENT_MEM=""
MONO_URL=""
DECOMP_URL=""
MONOLITH_TABLE=""
DECOMPOSED_TABLE=""
MONOLITH_FUNCTION=""
DECOMPOSED_CRUD_FUNCTION=""
ORCHESTRATOR_FUNCTION=""
LEXICAL_FUNCTION=""
READABILITY_FUNCTION=""
KEYWORDS_FUNCTION=""
DOC_ID=""

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; return 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    fail "comando não encontrado: $1"
    return 1
  }
}

for cmd in aws sam curl jq diff; do
  require_command "$cmd" || exit 1
done

[[ -f "$DATASET_FILE" ]] || { fail "dataset não encontrado: $DATASET_FILE"; exit 1; }
[[ -f "$SEED_SCRIPT" ]] || { fail "seed script não encontrado: $SEED_SCRIPT"; exit 1; }

get_stack_outputs() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs' \
    --output json
}

get_output_exact() {
  local key="$1"
  get_stack_outputs \
    | jq -r --arg key "$key" '.[] | select(.OutputKey == $key) | .OutputValue' \
    | head -n 1
}

resolve_api_url() {
  local key="$1"
  local value
  value="$(get_output_exact "$key")"
  [[ -n "$value" && "$value" != "null" ]] && { printf '%s\n' "${value%/}"; return 0; }
  local pattern
  if [[ "$key" == *Monolith* ]]; then pattern='monolith.*api.*url'; else pattern='decomposed.*api.*url'; fi
  get_stack_outputs \
    | jq -r --arg pattern "$pattern" '.[] | select((.OutputKey | ascii_downcase) | test($pattern)) | .OutputValue' \
    | head -n 1
}

resolve_function_by_logical_id() {
  local logical_id="$1"
  aws cloudformation list-stack-resources \
    --stack-name "$STACK_NAME" \
    --query "StackResourceSummaries[?LogicalResourceId=='$logical_id'].PhysicalResourceId | [0]" \
    --output text
}

resolve_function_by_pattern() {
  local pattern="$1"
  aws cloudformation list-stack-resources \
    --stack-name "$STACK_NAME" \
    --output json \
    | jq -r --arg pattern "$pattern" '
        .StackResourceSummaries[]
        | select(.ResourceType == "AWS::Lambda::Function")
        | select(.LogicalResourceId | test($pattern; "i"))
        | .PhysicalResourceId
      ' | head -n 1
}

resolve_function() {
  local logical_id="$1" pattern="$2" value
  value="$(resolve_function_by_logical_id "$logical_id" || true)"
  if [[ -n "$value" && "$value" != "None" ]]; then printf '%s\n' "$value"; return 0; fi
  value="$(resolve_function_by_pattern "$pattern" || true)"
  [[ -n "$value" ]] || { fail "não foi possível resolver Lambda: $logical_id"; return 1; }
  printf '%s\n' "$value"
}

resolve_stack_values() {
  MONO_URL="$(resolve_api_url "$MONO_URL_KEY")"
  DECOMP_URL="$(resolve_api_url "$DECOMP_URL_KEY")"
  MONOLITH_TABLE="$(get_output_exact "$MONO_TABLE_KEY")"
  DECOMPOSED_TABLE="$(get_output_exact "$DECOMP_TABLE_KEY")"

  [[ -n "$MONO_URL" && "$MONO_URL" != "null" ]] || { fail "Output $MONO_URL_KEY não encontrado"; return 1; }
  [[ -n "$DECOMP_URL" && "$DECOMP_URL" != "null" ]] || { fail "Output $DECOMP_URL_KEY não encontrado"; return 1; }
  [[ -n "$MONOLITH_TABLE" && "$MONOLITH_TABLE" != "null" ]] || { fail "Output $MONO_TABLE_KEY não encontrado"; return 1; }
  [[ -n "$DECOMPOSED_TABLE" && "$DECOMPOSED_TABLE" != "null" ]] || { fail "Output $DECOMP_TABLE_KEY não encontrado"; return 1; }

  MONOLITH_FUNCTION="$(resolve_function MonolithFunction 'Monolith.*Function')"
  DECOMPOSED_CRUD_FUNCTION="$(resolve_function DecomposedCrudFunction 'DecomposedCrud.*Function')"
  ORCHESTRATOR_FUNCTION="$(resolve_function DecomposedOrchestratorFunction 'DecomposedOrchestrator.*Function|Orchestrator.*Function')"
  LEXICAL_FUNCTION="$(resolve_function LexicalFunction 'Lexical.*Function')"
  READABILITY_FUNCTION="$(resolve_function ReadabilityFunction 'Readability.*Function')"
  KEYWORDS_FUNCTION="$(resolve_function KeywordsFunction 'Keywords.*Function')"

  echo "MONO_URL=$MONO_URL"
  echo "DECOMP_URL=$DECOMP_URL"
  echo "MONOLITH_TABLE=$MONOLITH_TABLE"
  echo "DECOMPOSED_TABLE=$DECOMPOSED_TABLE"
  echo "MONOLITH_FUNCTION=$MONOLITH_FUNCTION"
  echo "DECOMPOSED_CRUD_FUNCTION=$DECOMPOSED_CRUD_FUNCTION"
  echo "ORCHESTRATOR_FUNCTION=$ORCHESTRATOR_FUNCTION"
  echo "LEXICAL_FUNCTION=$LEXICAL_FUNCTION"
  echo "READABILITY_FUNCTION=$READABILITY_FUNCTION"
  echo "KEYWORDS_FUNCTION=$KEYWORDS_FUNCTION"
}

assert_lambda_config() {
  local role="$1" function_name="$2" expected_arch="$3" expected_memory="$4"
  local output_file="$TMP_ROOT/${role}.config.json"
  aws lambda get-function-configuration \
    --function-name "$function_name" \
    --query '{Runtime:Runtime,Architectures:Architectures,MemorySize:MemorySize,Timeout:Timeout}' \
    --output json > "$output_file"
  cat "$output_file"
  jq -e \
    --arg runtime "$EXPECTED_RUNTIME" \
    --arg arch "$expected_arch" \
    --argjson memory "$expected_memory" \
    --argjson timeout "$EXPECTED_TIMEOUT" \
    '(.Runtime == $runtime) and (.Architectures | length == 1) and (.Architectures[0] == $arch) and (.MemorySize == $memory) and (.Timeout == $timeout)' \
    "$output_file" >/dev/null || { fail "configuração inválida para $role ($function_name)"; return 1; }
}

verify_all_lambda_configs() {
  local arch="$1" mem="$2"
  log "Verificar configuração das Lambdas"
  assert_lambda_config monolith "$MONOLITH_FUNCTION" "$arch" "$mem"
  assert_lambda_config crud "$DECOMPOSED_CRUD_FUNCTION" "$arch" "$mem"
  assert_lambda_config orchestrator "$ORCHESTRATOR_FUNCTION" "$arch" "$mem"
  assert_lambda_config lexical "$LEXICAL_FUNCTION" "$arch" "$mem"
  assert_lambda_config readability "$READABILITY_FUNCTION" "$arch" "$mem"
  assert_lambda_config keywords "$KEYWORDS_FUNCTION" "$arch" "$mem"
}

get_dataset_document() {
  DOC_ID="$(jq -r '.[0].id' "$DATASET_FILE")"
  [[ -n "$DOC_ID" && "$DOC_ID" != "null" ]] || { fail "não foi possível extrair o primeiro id do dataset"; return 1; }
  jq -e '.[0] | has("id") and has("title") and has("body") and has("tags") and has("language")' "$DATASET_FILE" >/dev/null \
    || { fail "primeiro documento não contém id/title/body/tags/language"; return 1; }
  echo "Dataset: $DATASET_FILE"
  echo "Dataset document ID: $DOC_ID"
}

seed_one_table() {
  local table="$1"
  local label="$2"

  # IMPORTANT: seed_dynamodb.py requires --table TABLE.
  log "Seed $label: table=$table dataset=$DATASET_FILE"
  log "CMD: ${PYTHON_CMD[*]} $SEED_SCRIPT --table $table --dataset $DATASET_FILE"

  "${PYTHON_CMD[@]}" "$SEED_SCRIPT" \
    --table "$table" \
    --dataset "$DATASET_FILE"
}

seed_database() {
  [[ "$RUN_SEED" == "true" ]] || { log "Seed desabilitado (RUN_SEED=$RUN_SEED)"; return 0; }

  log "Popular DynamoDB com dataset $DATASET_VERSION"
  seed_one_table "$MONOLITH_TABLE" "monolith"
  seed_one_table "$DECOMPOSED_TABLE" "decomposed"
}

http_request() {
  local method="$1" url="$2" body_file="$3" request_body="${4:-}"
  case "$method" in
    GET)
      curl -sS --max-time "$HTTP_MAX_TIME" -o "$body_file" -w '%{http_code}' "$url" ;;
    DELETE)
      curl -sS --max-time "$HTTP_MAX_TIME" -X DELETE -o "$body_file" -w '%{http_code}' "$url" ;;
    *)
      curl -sS --max-time "$HTTP_MAX_TIME" -X "$method" -H 'Content-Type: application/json' -d "$request_body" -o "$body_file" -w '%{http_code}' "$url" ;;
  esac
}

assert_status() {
  local actual="$1" expected="$2" endpoint="$3" response_file="$4"
  [[ "$actual" == "$expected" ]] && return 0
  echo "Resposta para $endpoint:" >&2
  cat "$response_file" >&2 || true
  fail "$endpoint retornou HTTP $actual; esperado $expected"
}

smoke_get_dataset() {
  local base_url="$1"
  local label="$2"
  local output_file="$TMP_ROOT/${label}-get.json"
  local result_file="$RESULTS_DIR/${CURRENT_ARCH}_${CURRENT_MEM}_${label}_get.json"
  local status="" attempt
  for attempt in 1 2 3 4 5 6; do
    status="$(http_request GET "$base_url/documents/$DOC_ID" "$output_file")"
    [[ "$status" == "200" ]] && break
    sleep 2
  done
  assert_status "$status" 200 "$label GET /documents/$DOC_ID" "$output_file"
  jq -e --arg id "$DOC_ID" '.id == $id' "$output_file" >/dev/null || { fail "$label GET retornou outro id"; return 1; }
  jq -S . "$output_file" > "$result_file"
  echo "[$label] GET dataset document: OK"
}

smoke_analysis() {
  local base_url="$1"
  local label="$2"
  local output_file="$TMP_ROOT/${label}-analysis.json"
  local sorted_file="$RESULTS_DIR/${CURRENT_ARCH}_${CURRENT_MEM}_${label}_analysis.json"
  local status
  status="$(http_request GET "$base_url/documents/$DOC_ID/analysis" "$output_file")"
  assert_status "$status" 200 "$label GET /documents/$DOC_ID/analysis" "$output_file"
  jq -e '
    has("lexical") and has("readability") and has("keywords")
    and (.lexical | has("wordCount") and has("uniqueWordCount") and has("characterCount") and has("averageWordLength") and has("frequencyDistribution"))
    and (.readability | has("sentenceCount") and has("averageSentenceLength") and has("averageWordLength") and has("readabilityIndex"))
    and (.keywords | has("topKeywords") and has("keywordFrequencies"))
  ' "$output_file" >/dev/null || { fail "$label /analysis possui schema inesperado"; return 1; }
  jq -S . "$output_file" > "$sorted_file"
  echo "[$label] GET /analysis: OK"
}

compare_json() {
  local left="$1" right="$2" description="$3" diff_file="$TMP_ROOT/compare.diff"
  diff -u "$left" "$right" > "$diff_file" && { echo "$description: OK"; return 0; }
  echo "$description: FAILED" >&2
  cat "$diff_file" >&2
  return 1
}

smoke_crud() {
  local base_url="$1" label="$2"
  local response_file="$TMP_ROOT/${label}-crud-response.json"
  local get_file="$TMP_ROOT/${label}-crud-get.json"
  local analysis_file="$TMP_ROOT/${label}-crud-analysis.json"
  local delete_file="$TMP_ROOT/${label}-crud-delete.json"
  local not_found_file="$TMP_ROOT/${label}-crud-not-found.json"
  local status smoke_id

  local payload='{"title":"Smoke test","body":"AWS Lambda serverless benchmark smoke test.","language":"en","tags":["smoke","aws"]}'
  local update='{"title":"Updated smoke test","body":"Updated AWS Lambda smoke test content.","language":"en","tags":["smoke","updated"]}'

  status="$(http_request POST "$base_url/documents" "$response_file" "$payload")"
  assert_status "$status" 201 "$label POST /documents" "$response_file"
  smoke_id="$(jq -r '.id' "$response_file")"
  [[ -n "$smoke_id" && "$smoke_id" != "null" ]] || { fail "$label POST não retornou id"; return 1; }

  status="$(http_request GET "$base_url/documents/$smoke_id" "$get_file")"
  assert_status "$status" 200 "$label GET /documents/$smoke_id" "$get_file"

  status="$(http_request PUT "$base_url/documents/$smoke_id" "$response_file" "$update")"
  assert_status "$status" 200 "$label PUT /documents/$smoke_id" "$response_file"

  status="$(http_request GET "$base_url/documents/$smoke_id/analysis" "$analysis_file")"
  assert_status "$status" 200 "$label GET /documents/$smoke_id/analysis" "$analysis_file"
  jq -e 'has("lexical") and has("readability") and has("keywords")' "$analysis_file" >/dev/null || { fail "$label analysis após CRUD possui schema inesperado"; return 1; }

  status="$(http_request DELETE "$base_url/documents/$smoke_id" "$delete_file")"
  assert_status "$status" 204 "$label DELETE /documents/$smoke_id" "$delete_file"

  status="$(http_request GET "$base_url/documents/$smoke_id" "$not_found_file")"
  assert_status "$status" 404 "$label GET deleted /documents/$smoke_id" "$not_found_file"
  echo "[$label] CRUD smoke: OK"
}

recent_log_report_count() {
  local function_name="$1"
  local start_epoch_ms="$2"
  local count

  count="$(
    aws logs filter-log-events \
      --log-group-name "/aws/lambda/$function_name" \
      --start-time "$start_epoch_ms" \
      --filter-pattern 'REPORT' \
      --output json 2>/dev/null |
      jq -r '.events | length'
  )"

  [[ "$count" =~ ^[0-9]+$ ]] && printf '%s\n' "$count" || printf '0\n'
}

verify_decomposed_logs() {
  local start_epoch_ms="$1" function_name count attempt
  log "Verificar execução das Lambdas decompostas via CloudWatch Logs"
  for function_name in "$ORCHESTRATOR_FUNCTION" "$LEXICAL_FUNCTION" "$READABILITY_FUNCTION" "$KEYWORDS_FUNCTION"; do
    count=0
    for attempt in $(seq 1 "$LOG_WAIT_ATTEMPTS"); do
      count="$(recent_log_report_count "$function_name" "$start_epoch_ms")"
      (( count > 0 )) && break
      sleep "$LOG_WAIT_SECONDS"
    done
    (( count > 0 )) || { fail "nenhum REPORT recente encontrado para $function_name"; return 1; }
    echo "[$function_name] REPORTs recentes: $count"
  done
}

run_smoke_cell() {
  local arch="$1" mem="$2" decomp_analysis_start_ms
  CURRENT_ARCH="$arch"
  CURRENT_MEM="$mem"

  echo
  echo "================================================================"
  echo "Smoke test: architecture=$arch memory=${mem}MB"
  echo "================================================================"

  log "sam build"
  sam build

  log "sam deploy"
  sam deploy \
    --parameter-overrides \
    LambdaMemoryMb="$mem" \
    LambdaArchitecture="$arch" \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset

  log "Verificar o stack"
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs' --output table
  resolve_stack_values
  verify_all_lambda_configs "$arch" "$mem"

  get_dataset_document

  # Preflight: this is printed before the actual seed call so the log proves
  # which table is being passed to seed_dynamodb.py.
  log "Preflight seed"
  echo "SEED_SCRIPT=$SEED_SCRIPT"
  echo "PYTHON=${PYTHON_CMD[*]}"
  echo "MONOLITH_TABLE=$MONOLITH_TABLE"
  echo "DECOMPOSED_TABLE=$DECOMPOSED_TABLE"
  echo "DATASET_FILE=$DATASET_FILE"
  seed_database

  log "Smoke test GET /documents/{id}"
  smoke_get_dataset "$MONO_URL" "monolith"
  smoke_get_dataset "$DECOMP_URL" "decomposed"
  compare_json \
    "$RESULTS_DIR/${CURRENT_ARCH}_${CURRENT_MEM}_monolith_get.json" \
    "$RESULTS_DIR/${CURRENT_ARCH}_${CURRENT_MEM}_decomposed_get.json" \
    "Document equivalence"

  log "Smoke test GET /documents/{id}/analysis"
  smoke_analysis "$MONO_URL" "monolith"
  decomp_analysis_start_ms=$(( ($(date +%s) - 30) * 1000 ))
  smoke_analysis "$DECOMP_URL" "decomposed"
  compare_json \
    "$RESULTS_DIR/${CURRENT_ARCH}_${CURRENT_MEM}_monolith_analysis.json" \
    "$RESULTS_DIR/${CURRENT_ARCH}_${CURRENT_MEM}_decomposed_analysis.json" \
    "/analysis functional equivalence"

  log "Smoke test CRUD completo"
  smoke_crud "$MONO_URL" "monolith"
  smoke_crud "$DECOMP_URL" "decomposed"

  if [[ "$VERIFY_DECOMPOSED_LOGS" == "true" ]]; then
    verify_decomposed_logs "$decomp_analysis_start_ms"
  fi

  echo "PASS: architecture=$arch memory=${mem}MB"
}

main() {
  local arch mem log_file rc failures=0
  echo "==============================================================="
  echo "Document API Benchmark - Lambda Smoke Test Matrix"
  echo "==============================================================="
  echo "Project root   : $PROJECT_ROOT"
  echo "Stack          : $STACK_NAME"
  echo "Dataset        : $DATASET_FILE"
  echo "Seed script     : $SEED_SCRIPT"
  echo "Python runner   : ${PYTHON_CMD[*]}"
  echo "Memories        : ${MEMORIES[*]}"
  echo "Architectures   : ${ARCHITECTURES[*]}"

  for arch in "${ARCHITECTURES[@]}"; do
    for mem in "${MEMORIES[@]}"; do
      log_file="$RESULTS_DIR/${arch}_${mem}.log"
      set +e
      (
        set -Eeuo pipefail
        run_smoke_cell "$arch" "$mem"
      ) 2>&1 | tee "$log_file"
      rc=${PIPESTATUS[0]}
      set -e

      if [[ "$rc" -eq 0 ]]; then
        printf '%s\t%s\tPASS\t-\n' "$arch" "$mem" >> "$SUMMARY_FILE"
      else
        failures=$((failures + 1))
        printf '%s\t%s\tFAIL\tsee %s\n' "$arch" "$mem" "$log_file" >> "$SUMMARY_FILE"
        echo "FAIL: architecture=$arch memory=${mem}MB (see $log_file)" >&2
      fi
    done
  done

  echo
  echo "==============================================================="
  echo "Resumo"
  echo "==============================================================="
  cat "$SUMMARY_FILE"

  (( failures == 0 )) || return 1
}

main "$@"
