"use client";

import { type FormEvent, useState } from "react";

import { ApiError, apiPost } from "@/lib/api";
import { pct } from "@/lib/format";
import type { BacktestResponse } from "@/lib/types";
import { EquityChart } from "@/components/EquityChart";
import { ErrorBanner, Field, StatCard } from "@/components/ui";

function defaultDates() {
  const end = new Date();
  const start = new Date();
  start.setFullYear(start.getFullYear() - 1);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { start: fmt(start), end: fmt(end) };
}

export default function BacktestPage() {
  const { start: defaultStart, end: defaultEnd } = defaultDates();
  const [start, setStart] = useState(defaultStart);
  const [end, setEnd] = useState(defaultEnd);
  const [cash, setCash] = useState(100000);
  const [maxPositions, setMaxPositions] = useState(5);
  const [universe, setUniverse] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResponse | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const universeList = universe.trim()
        ? universe
            .split(",")
            .map((s) => s.trim().toUpperCase())
            .filter(Boolean)
        : undefined;
      const res = await apiPost<BacktestResponse>("backtest", {
        start,
        end,
        cash,
        max_positions: maxPositions,
        universe: universeList,
      });
      setResult(res);
    } catch (err) {
      if (err instanceof ApiError) setError(ApiError.detailToMessage(err.detail));
      else setError(err instanceof Error ? err.message : "回测失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <form
        onSubmit={onSubmit}
        className="grid grid-cols-2 gap-4 rounded-md border border-slate-200 bg-white p-4 sm:grid-cols-5"
      >
        <Field label="Start">
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            required
            className="input"
          />
        </Field>
        <Field label="End">
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            required
            className="input"
          />
        </Field>
        <Field label="Initial cash">
          <input
            type="number"
            min={1}
            value={cash}
            onChange={(e) => setCash(Number(e.target.value))}
            className="input"
          />
        </Field>
        <Field label="Max positions">
          <input
            type="number"
            min={1}
            value={maxPositions}
            onChange={(e) => setMaxPositions(Number(e.target.value))}
            className="input"
          />
        </Field>
        <Field label="Universe (optional)">
          <input
            type="text"
            placeholder="AAPL,MSFT,..."
            value={universe}
            onChange={(e) => setUniverse(e.target.value)}
            className="input"
          />
        </Field>
        <div className="col-span-2 flex items-end sm:col-span-5">
          <button
            type="submit"
            disabled={loading}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {loading ? "运行中…" : "运行回测"}
          </button>
        </div>
      </form>

      {error && <ErrorBanner message={error} />}
      {loading && <p className="text-sm text-slate-500">回测运行中,涉及拉取历史行情,可能需要一些时间…</p>}

      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <StatCard label="Total return" value={pct(result.metrics.total_return)} />
            <StatCard label="Max drawdown" value={pct(result.metrics.max_drawdown)} />
            <StatCard label="Sharpe" value={result.metrics.sharpe.toFixed(2)} />
            <StatCard label="Win rate" value={pct(result.metrics.win_rate)} />
            <StatCard label="Fills" value={String(result.metrics.num_fills)} />
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4">
            <h2 className="mb-2 text-sm font-semibold text-slate-700">Equity curve</h2>
            {result.equity_curve.length > 0 ? (
              <EquityChart data={result.equity_curve} />
            ) : (
              <p className="text-sm text-slate-500">无数据</p>
            )}
          </div>

          {result.skipped.length > 0 && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
              <p className="font-medium">跳过的标的 ({result.skipped.length})</p>
              <ul className="mt-1 list-disc pl-5">
                {result.skipped.map((s) => (
                  <li key={s.symbol}>
                    {s.symbol}: {s.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
