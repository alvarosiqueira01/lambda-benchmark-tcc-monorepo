import http from "k6/http";
import exec from "k6/execution";
import { Counter, Gauge, Trend } from "k6/metrics";

const runId = __ENV.RUN_ID || "UNSET";
const pattern = __ENV.LOAD_PATTERN || "constant";
const targetUrl = __ENV.TARGET_URL || "";
const outDir = __ENV.OUT_DIR || ".";
const warmupSeconds = Number(__ENV.WARMUP_SECONDS || 120);
const measurementSeconds = Number(__ENV.MEASUREMENT_SECONDS || 120);
const totalSeconds = warmupSeconds + measurementSeconds;
const preAllocatedVUs = Number(__ENV.PREALLOCATED_VUS || 100);
const maxVUs = Number(__ENV.MAX_VUS || 500);

const TIMEOUT_ERROR_CODES = new Set([1050, 1211]);

const T0_SOURCE_SCENARIO = "k6_exec_scenario_startTime";
const T0_SOURCE_FALLBACK = "generator_date_now_fallback";

const scenarioStart = { value: 0 };
const scenarioStartState = { initialized: false, source: null };

const scheduledCounter = new Counter("scheduled_iterations");
const startedCounter = new Counter("external_http_started");
const responseCounter = new Counter("external_http_responses");
const newConnectionCounter = new Counter("external_http_new_connection");
const newTlsCounter = new Counter("external_http_new_tls");
const requestLatency = new Trend("bench_request_latency", true);
const connectTime = new Trend("bench_connect_ms", true);
const tlsTime = new Trend("bench_tls_ms", true);
const sendingTime = new Trend("bench_sending_ms", true);
const waitingTime = new Trend("bench_waiting_ms", true);
const receivingTime = new Trend("bench_receiving_ms", true);
const t0Gauge = new Gauge("scenario_start_epoch_ms");
const t0ScenarioCounter = new Counter("t0_source_k6_exec_scenario_startTime");
const t0FallbackCounter = new Counter("t0_source_generator_date_now_fallback");

function t0Ms() {
  if (scenarioStartState.initialized) return scenarioStart.value;
  const startTime = exec.scenario.startTime;
  const fromScenario = typeof startTime === "number" && startTime > 0;
  scenarioStart.value = fromScenario ? startTime : Date.now();
  scenarioStartState.source = fromScenario
    ? T0_SOURCE_SCENARIO
    : T0_SOURCE_FALLBACK;
  scenarioStartState.initialized = true;
  if (fromScenario) {
    t0ScenarioCounter.add(1);
  } else {
    t0FallbackCounter.add(1);
  }
  t0Gauge.add(scenarioStart.value, {
    run_id: runId,
    pattern,
    t0_source: scenarioStartState.source,
  });
  return scenarioStart.value;
}

function baseTags(iteration) {
  const t0 = t0Ms();
  return {
    run_id: runId,
    load_pattern: pattern,
    iteration: String(iteration),
    t0_epoch_ms: String(t0),
    t0_source: scenarioStartState.source,
  };
}

function scheduledOffsetMs(iteration) {
  if (pattern === "constant") return iteration * 50;
  if (pattern === "burst") return iteration * 10;
  return iteration * 200;
}

function callApi(iteration) {
  const start = Date.now();
  const t0 = t0Ms();
  const tags = {
    ...baseTags(iteration),
    sample_id: String(iteration),
    scheduled_offset_ms: String(scheduledOffsetMs(iteration)),
  };
  startedCounter.add(1, tags);
  const response = http.get(targetUrl, {
    headers: { "X-Benchmark-Run-Id": runId },
    tags: { run_id: runId, load_pattern: pattern },
    timeout: "30s",
  });
  const elapsed = Date.now() - start;
  const offset = start - t0;
  const phase =
    offset < warmupSeconds * 1000
      ? "warmup"
      : offset < totalSeconds * 1000
        ? "measurement"
        : "outside_run";
  const errorCode = Number(response.error_code || 0);
  const pointTags = {
    ...tags,
    phase,
    status_code: String(response.status),
    error_code: String(errorCode),
    error: String(response.error || ""),
    timeout: String(TIMEOUT_ERROR_CODES.has(errorCode)),
    request_sent: "true",
    target_url: targetUrl,
  };
  responseCounter.add(1, {
    run_id: runId,
    load_pattern: pattern,
    status: String(response.status),
    error_code: String(errorCode),
  });
  requestLatency.add(elapsed, pointTags);
  const timings = response.timings || {};
  connectTime.add(timings.connecting || 0, pointTags);
  tlsTime.add(timings.tls_handshaking || 0, pointTags);
  sendingTime.add(timings.sending || 0, pointTags);
  waitingTime.add(timings.waiting || 0, pointTags);
  receivingTime.add(timings.receiving || 0, pointTags);
  const newConnection = (timings.connecting || 0) > 0;
  const newTls = (timings.tls_handshaking || 0) > 0;
  if (newConnection) newConnectionCounter.add(1, pointTags);
  if (newTls) newTlsCounter.add(1, pointTags);
  return { start, elapsed, response, phase, newConnection, newTls };
}

function scenarioFor(rate) {
  return {
    executor: "constant-arrival-rate",
    rate,
    timeUnit: "1s",
    duration: `${totalSeconds}s`,
    preAllocatedVUs,
    maxVUs,
    exec: "arrivalIteration",
  };
}

export const options = (() => {
  if (pattern === "constant") {
    return { scenarios: { constant: scenarioFor(20) }, thresholds: {} };
  }
  if (pattern === "burst") {
    return { scenarios: { burst: scenarioFor(100) }, thresholds: {} };
  }
  if (pattern === "sparse_miniburst") {
    return { scenarios: { sparse: scenarioFor(5) }, thresholds: {} };
  }
  throw new Error(`Unsupported LOAD_PATTERN: ${pattern}`);
})();

// One open-loop scenario at the full arrival rate. Iterations in idle windows
// do not perform HTTP. The upper bounds prevent the extra iteration that
// constant-arrival-rate schedules exactly at the end of the duration.
export function arrivalIteration() {
  const i = exec.scenario.iterationInTest;
  const tags = baseTags(i);
  scheduledCounter.add(1, tags);
  t0Ms();

  if (pattern === "constant") {
    if (i < 4800) callApi(i);
    return;
  }
  if (pattern === "burst") {
    // 100/s for 5 s cycles: HTTP only in the first second of each cycle.
    if (i < 24000 && i % 500 < 100) callApi(i);
    return;
  }
  // sparse_miniburst: 5/s for 10 s cycles: HTTP only at 0, 200, 400, 600, 800 ms.
  if (i < 1200 && i % 50 < 5) callApi(i);
}

export function handleSummary(data) {
  // Each k6 VU has an isolated JS runtime, so the module-level scenarioStart
  // object is not shared with the summary runtime. The Gauge and Counter
  // metrics are.
  const gaugeT0 = data.metrics.scenario_start_epoch_ms?.values?.value;
  const scenarioT0Count =
    data.metrics.t0_source_k6_exec_scenario_startTime?.values?.count || 0;
  const fallbackT0Count =
    data.metrics.t0_source_generator_date_now_fallback?.values?.count || 0;
  let t0Source = "unknown";
  if (fallbackT0Count > 0) {
    t0Source = scenarioT0Count > 0 ? "mixed" : T0_SOURCE_FALLBACK;
  } else if (scenarioT0Count > 0) {
    t0Source = T0_SOURCE_SCENARIO;
  }
  const summary = {
    run_id: runId,
    pattern,
    target_url: targetUrl,
    t0_epoch_ms: gaugeT0 || scenarioStart.value || null,
    t0_source: t0Source,
    t0_source_counts: {
      k6_exec_scenario_startTime: scenarioT0Count,
      generator_date_now_fallback: fallbackT0Count,
    },
    warmup_seconds: warmupSeconds,
    measurement_seconds: measurementSeconds,
    total_seconds: totalSeconds,
    pre_allocated_vus: preAllocatedVUs,
    max_vus: maxVUs,
    generated_at_utc: new Date().toISOString(),
    metrics: data.metrics,
    root_group: data.root_group,
    state: data.state,
  };
  return {
    [`${outDir}/k6-summary.json`]: JSON.stringify(summary, null, 2),
  };
}
