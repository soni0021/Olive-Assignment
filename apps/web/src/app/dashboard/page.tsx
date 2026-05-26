import Link from "next/link";

import { fetchCost, fetchErrors, fetchLatency, fetchThroughput } from "@/lib/ingestion";

export const dynamic = "force-dynamic";

function fmt(ms: number | null): string {
  if (ms == null) return "—";
  return `${ms.toFixed(0)} ms`;
}

function fmtUsd(n: number): string {
  return `$${n.toFixed(4)}`;
}

export default async function DashboardPage() {
  const [latency, throughput, errors, cost] = await Promise.all([
    fetchLatency(24).catch(() => []),
    fetchThroughput(24).catch(() => []),
    fetchErrors(24).catch(() => []),
    fetchCost(14).catch(() => []),
  ]);

  const totalCalls = latency.reduce((sum, r) => sum + r.n, 0);
  const totalErrors = errors.filter((e) => e.status !== "SUCCESS").reduce((s, e) => s + e.n, 0);
  const errorRate = totalCalls > 0 ? (totalErrors / (totalCalls + totalErrors)) * 100 : 0;
  const totalCost = cost.reduce((s, r) => s + r.cost_usd, 0);

  return (
    <main>
      <header>
        <h1>llm-observe dashboard</h1>
        <nav>
          <Link href="/">Chat</Link>
          <Link href="/dashboard">Dashboard</Link>
        </nav>
      </header>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 24 }}>
        <Stat label="Calls (24h)" value={totalCalls.toLocaleString()} />
        <Stat label="Error rate" value={`${errorRate.toFixed(2)} %`} />
        <Stat label="Cost (14d)" value={fmtUsd(totalCost)} />
        <Stat label="Models active" value={String(new Set(latency.map((r) => r.model)).size)} />
      </section>

      <h2 style={{ fontSize: 14, color: "#9aa6b2", margin: "12px 0 6px" }}>
        Latency / TTFT by provider / model (last 24h, SUCCESS only)
      </h2>
      <table>
        <thead>
          <tr>
            <th>Provider</th><th>Model</th><th>n</th>
            <th>p50</th><th>p95</th><th>p99</th>
            <th>p50 TTFT</th><th>p95 TTFT</th>
          </tr>
        </thead>
        <tbody>
          {latency.length === 0 && <tr><td colSpan={8} style={{ color: "#9aa6b2" }}>No data yet — run some chat traffic.</td></tr>}
          {latency.map((r, i) => (
            <tr key={i}>
              <td>{r.provider}</td><td>{r.model}</td><td>{r.n}</td>
              <td>{fmt(r.p50_latency)}</td><td>{fmt(r.p95_latency)}</td><td>{fmt(r.p99_latency)}</td>
              <td>{fmt(r.p50_ttft)}</td><td>{fmt(r.p95_ttft)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ fontSize: 14, color: "#9aa6b2", margin: "24px 0 6px" }}>
        Throughput (output tokens per second, weighted mean)
      </h2>
      <table>
        <thead>
          <tr>
            <th>Provider</th><th>Model</th><th>n</th>
            <th>Tokens out (total)</th><th>tokens / sec</th>
          </tr>
        </thead>
        <tbody>
          {throughput.length === 0 && <tr><td colSpan={5} style={{ color: "#9aa6b2" }}>—</td></tr>}
          {throughput.map((r, i) => (
            <tr key={i}>
              <td>{r.provider}</td><td>{r.model}</td><td>{r.n}</td>
              <td>{r.total_output_tokens.toLocaleString()}</td>
              <td>{r.tokens_per_second.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ fontSize: 14, color: "#9aa6b2", margin: "24px 0 6px" }}>
        Cost by day / model (last 14 days)
      </h2>
      <table>
        <thead>
          <tr><th>Day</th><th>Provider</th><th>Model</th><th>Cost (USD)</th><th>Calls</th></tr>
        </thead>
        <tbody>
          {cost.length === 0 && <tr><td colSpan={5} style={{ color: "#9aa6b2" }}>—</td></tr>}
          {cost.map((r, i) => (
            <tr key={i}>
              <td>{r.day}</td><td>{r.provider}</td><td>{r.model}</td>
              <td>{fmtUsd(r.cost_usd)}</td><td>{r.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: "#14181d", border: "1px solid #1f242a", borderRadius: 8, padding: 12 }}>
      <div style={{ fontSize: 11, color: "#9aa6b2", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, marginTop: 4 }}>{value}</div>
    </div>
  );
}
