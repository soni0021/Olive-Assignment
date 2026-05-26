// Server-side client for the ingestion analytics endpoints. Used by the
// dashboard pages — never imported from "use client" components.

const INGESTION_BASE = process.env.OBSERVE_INGESTION_BASE ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const url = `${INGESTION_BASE}${path}`;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`ingestion ${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export interface LatencyRow {
  provider: string;
  model: string;
  n: number;
  p50_latency: number | null;
  p95_latency: number | null;
  p99_latency: number | null;
  p50_ttft: number | null;
  p95_ttft: number | null;
  p99_ttft: number | null;
}

export interface ThroughputRow {
  provider: string;
  model: string;
  n: number;
  total_output_tokens: number;
  total_latency_s: number;
  tokens_per_second: number;
}

export interface ErrorBucket { hour: string; status: string; n: number; }
export interface CostRow { day: string; provider: string; model: string; cost_usd: number; n: number; }

export const fetchLatency = (hours = 24) => get<LatencyRow[]>(`/v1/analytics/latency?hours=${hours}`);
export const fetchThroughput = (hours = 24) => get<ThroughputRow[]>(`/v1/analytics/throughput?hours=${hours}`);
export const fetchErrors = (hours = 24) => get<ErrorBucket[]>(`/v1/analytics/errors?hours=${hours}`);
export const fetchCost = (days = 14) => get<CostRow[]>(`/v1/analytics/cost?days=${days}`);
