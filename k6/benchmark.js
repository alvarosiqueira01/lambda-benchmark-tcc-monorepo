import http from "k6/http";
import { sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const runId = __ENV.RUN_ID || "UNSET";
const pattern = __ENV.LOAD_PATTERN || "constant";
const targetUrl = __ENV.TARGET_URL || "";
const outDir = __ENV.OUT_DIR || ".";
const t0EpochMs = Number(__ENV.T0_EPOCH_MS || Date.now());
const startedCounter = new Counter("external_http_started");
const responseCounter = new Counter("external_http_responses");
const requestLatency = new Trend("bench_request_latency", true);

function callApi() {
  const start = Date.now();
  startedCounter.add(1, { run_id: runId, pattern });
  const response = http.get(targetUrl, {
    headers: { "X-Benchmark-Run-Id": runId },
    tags: { run_id: runId, load_pattern: pattern },
    timeout: "30s",
  });
  responseCounter.add(1, { run_id: runId, pattern, status: String(response.status) });
  const phase =
    start - t0EpochMs < 120000
      ? "warmup"
      : start - t0EpochMs < 240000
      ? "measurement"
      : "outside_run";
  requestLatency.add(Date.now() - start, {
    run_id: runId, pattern, status_code: String(response.status),
    request_sent: "true", timeout: String(response.status === 0), phase,
    target_url: targetUrl,
  });
  return { start, response };
}

export const options = (() => {
  if (pattern === "constant") {
    return {
      scenarios: {
        constant: {
          executor: "constant-arrival-rate", rate: 20, timeUnit: "1s",
          duration: "240s", preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 100),
          maxVUs: Number(__ENV.MAX_VUS || 500), exec: "constantRequest",
        },
      },
      thresholds: {},
    };
  }
  const scenarios = {};
  if (pattern === "burst") {
    for (let i = 0; i < 48; i++)
      scenarios[`burst_${String(i).padStart(2, "0")}`] = {
        executor: "constant-arrival-rate", startTime: `${i * 5}s`, rate: 100,
        timeUnit: "1s", duration: "1s", preAllocatedVUs: 100, maxVUs: 500,
        exec: "scheduledRequest",
      };
  } else if (pattern === "sparse_miniburst") {
    for (let i = 0; i < 24; i++)
      scenarios[`sparse_${String(i).padStart(2, "0")}`] = {
        executor: "constant-arrival-rate", startTime: `${i * 10}s`, rate: 5,
        timeUnit: "1s", duration: "1s", preAllocatedVUs: 10, maxVUs: 100,
        exec: "scheduledRequest",
      };
  } else {
    throw new Error(`Unsupported LOAD_PATTERN: ${pattern}`);
  }
  scenarios.keepalive = {
    executor: "constant-vus", startTime: "239s", duration: "1s",
    vus: 1, exec: "keepAlive",
  };
  return { scenarios, thresholds: {} };
})();

export function constantRequest() { callApi(); }
export function scheduledRequest() { callApi(); }
export function keepAlive() { sleep(1); }

export function handleSummary(data) {
  return {
    [`${outDir}/k6-summary.json`]: JSON.stringify({
      run_id: runId, pattern, target_url: targetUrl,
      generated_at_utc: new Date().toISOString(),
      metrics: data.metrics, root_group: data.root_group, state: data.state,
    }, null, 2),
  };
}