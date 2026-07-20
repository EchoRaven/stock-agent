"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import { money } from "@/lib/format";
import type { DashboardResponse } from "@/lib/types";
import { ErrorBanner, StatCard, Th } from "@/components/ui";

const POLL_MS = 15000;

export default function DashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await apiGet<DashboardResponse>("dashboard");
      setData(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  if (loading && !data) return <p className="text-sm text-slate-500">Loading…</p>;
  if (error && !data) return <ErrorBanner message={error} />;
  if (!data) return null;

  const positions = Object.entries(data.positions);

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      {data.circuit_breaker_tripped && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
          熔断已触发(circuit breaker tripped)— 交易已暂停
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Mode" value={data.mode} />
        <StatCard label="Equity" value={money(data.equity)} />
        <StatCard label="Cash" value={money(data.cash)} />
        <Link href="/orders" className="block">
          <StatCard
            label="Pending orders"
            value={String(data.pending_orders_count)}
            accent={data.pending_orders_count > 0}
          />
        </Link>
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Positions</h2>
        {positions.length === 0 ? (
          <p className="text-sm text-slate-500">当前无持仓</p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <Th>Symbol</Th>
                  <Th align="right">Shares</Th>
                  <Th align="right">Avg cost</Th>
                  <Th align="right">Value</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {positions.map(([symbol, p]) => (
                  <tr key={symbol}>
                    <td className="px-3 py-2 font-medium text-slate-900">{symbol}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{p.shares}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{money(p.avg_cost)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {money(p.shares * p.avg_cost)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-xs text-slate-400">as of {data.as_of}</p>
    </div>
  );
}
