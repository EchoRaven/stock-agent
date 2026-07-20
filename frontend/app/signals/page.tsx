"use client";

import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import type { SignalResponse } from "@/lib/types";
import { ErrorBanner, Th } from "@/components/ui";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function SignalsPage() {
  const [date, setDate] = useState(today());
  const [signals, setSignals] = useState<SignalResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiGet<SignalResponse[]>(`signals?date=${date}`)
      .then((res) => {
        if (!cancelled) setSignals(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "failed to load signals");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [date]);

  const partKeys = Array.from(new Set((signals ?? []).flatMap((s) => Object.keys(s.parts))));

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-slate-700" htmlFor="signal-date">
          Date
        </label>
        <input
          id="signal-date"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="input"
        />
      </div>

      {error && <ErrorBanner message={error} />}
      {loading && <p className="text-sm text-slate-500">Loading…</p>}

      {!loading && !error && signals && signals.length === 0 && (
        <p className="text-sm text-slate-500">该日无信号(先跑 screen/cron)</p>
      )}

      {!loading && signals && signals.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <Th>Rank</Th>
                <Th>Symbol</Th>
                <Th align="right">Total</Th>
                {partKeys.map((k) => (
                  <Th key={k} align="right">
                    {k}
                  </Th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {signals.map((s) => (
                <tr key={s.symbol}>
                  <td className="px-3 py-2 tabular-nums">{s.rank}</td>
                  <td className="px-3 py-2 font-medium text-slate-900">{s.symbol}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{s.total.toFixed(4)}</td>
                  {partKeys.map((k) => (
                    <td key={k} className="px-3 py-2 text-right tabular-nums">
                      {s.parts[k] !== undefined ? s.parts[k].toFixed(4) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
