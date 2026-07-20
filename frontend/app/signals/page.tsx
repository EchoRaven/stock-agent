"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { SignalResponse } from "@/lib/types";
import { ErrorBanner, Th } from "@/components/ui";
import { SentimentWidget } from "@/components/SentimentWidget";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function SignalsPage() {
  const [date, setDate] = useState(today());
  const [signals, setSignals] = useState<SignalResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

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

  async function onRunScreen() {
    setRunBusy(true);
    setRunError(null);
    try {
      const res = await apiPost<SignalResponse[]>("signals/run", {});
      setSignals(res);
      setDate(today());
    } catch (err) {
      setRunError(
        err instanceof ApiError ? ApiError.detailToMessage(err.detail) : "运行筛选失败"
      );
    } finally {
      setRunBusy(false);
    }
  }

  const partKeys = Array.from(new Set((signals ?? []).flatMap((s) => Object.keys(s.parts))));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
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
        <button
          onClick={onRunScreen}
          disabled={runBusy}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {runBusy ? "运行中…" : "现在跑一次"}
        </button>
        {runBusy && (
          <span className="text-sm text-slate-500">正在联网拉取行情并筛选,可能需要一些时间…</span>
        )}
      </div>
      {runError && <p className="text-sm text-red-600">{runError}</p>}

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
                  <td className="px-3 py-2 font-medium">
                    <Link
                      href={`/stock/${s.symbol}`}
                      className="text-slate-900 underline decoration-slate-300 underline-offset-2 hover:text-indigo-700 hover:decoration-indigo-400"
                    >
                      {s.symbol}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{s.total.toFixed(4)}</td>
                  {partKeys.map((k) => (
                    <td
                      key={k}
                      className="px-3 py-2 text-right tabular-nums"
                      title={s.parts[k]?.detail ?? ""}
                    >
                      {typeof s.parts[k]?.score === "number"
                        ? s.parts[k].score.toFixed(4)
                        : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <SentimentWidget />
    </div>
  );
}
