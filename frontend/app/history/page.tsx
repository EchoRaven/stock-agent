"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, apiGet } from "@/lib/api";
import { money, pct } from "@/lib/format";
import type { DecisionHistoryItem, MemoryEntry, PerformanceResponse } from "@/lib/types";
import { ErrorBanner, StatCard, Th } from "@/components/ui";
import { PriceChart } from "@/components/PriceChart";

const CHAIR_VERDICT_SNIPPET_LEN = 120;
const REVIEW_SNIPPET_LEN = 140;

interface TradeReviewEvidence {
  realized_pnl?: number;
  realized_pnl_pct?: number;
  holding_days?: number;
}

/** evidence_json is a freeform JSON string written by the reflection service —
 * parse defensively, never throw on missing/malformed data. */
function parseEvidence(raw: string | null): TradeReviewEvidence {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as TradeReviewEvidence;
    }
    return {};
  } catch {
    return {};
  }
}

function pnlColor(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "text-slate-500";
  return x > 0 ? "text-emerald-700" : x < 0 ? "text-red-700" : "text-slate-500";
}

function signedMoney(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const sign = x > 0 ? "+" : "";
  return `${sign}${money(x)}`;
}

/** realized_pnl_pct is already stored in percentage-point units (reflection_
 * service computes `realized_pnl / denom * 100`) — don't multiply again. */
function signedPctPoints(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const sign = x > 0 ? "+" : "";
  return `${sign}${x.toFixed(digits)}%`;
}

function truncate(text: string, max: number): string {
  const trimmed = text.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1)}…` : trimmed;
}

const ACTION_COLORS: Record<string, string> = {
  buy: "text-emerald-700",
  sell: "text-red-700",
  hold: "text-slate-500",
};

function errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return ApiError.detailToMessage(err.detail);
  return err instanceof Error ? err.message : fallback;
}

export default function HistoryPage() {
  const [perf, setPerf] = useState<PerformanceResponse | null>(null);
  const [perfError, setPerfError] = useState<string | null>(null);

  const [trades, setTrades] = useState<MemoryEntry[] | null>(null);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const [decisions, setDecisions] = useState<DecisionHistoryItem[] | null>(null);
  const [decisionsError, setDecisionsError] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    Promise.all([
      apiGet<PerformanceResponse>("performance")
        .then((res) => {
          if (!cancelled) {
            setPerf(res);
            setPerfError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setPerfError(errMessage(err, "业绩数据加载失败"));
        }),
      apiGet<MemoryEntry[]>("memory?kind=trade_review")
        .then((res) => {
          if (!cancelled) {
            setTrades(res);
            setTradesError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setTradesError(errMessage(err, "已平仓复盘加载失败"));
        }),
      apiGet<DecisionHistoryItem[]>("decisions?limit=50")
        .then((res) => {
          if (!cancelled) {
            setDecisions(res);
            setDecisionsError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setDecisionsError(errMessage(err, "决策历史加载失败"));
        }),
    ]).finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  if (loading && !perf && !trades && !decisions) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }

  const chartData = (perf?.cumulative_pnl_series ?? []).map((p) => ({
    date: p.date,
    close: p.cum_pnl,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">业绩 / History</h1>
        <p className="text-sm text-slate-500">
          模拟盘战绩单 — 已实现盈亏、累计曲线、逐笔平仓复盘与历史委员会决策,用于判断委员会长期是否靠谱。
        </p>
      </div>

      {perfError && <ErrorBanner message={perfError} />}

      {perf && (
        <section className="space-y-2">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatCard
              label="已实现总盈亏"
              value={signedMoney(perf.realized_pnl_total)}
              valueClassName={pnlColor(perf.realized_pnl_total)}
            />
            <StatCard label="胜率" value={perf.win_rate === null ? "—" : pct(perf.win_rate, 0)} />
            <StatCard label="已平仓交易" value={String(perf.closed_trades)} />
            <StatCard
              label="平均持有天数"
              value={perf.avg_holding_days === null ? "—" : perf.avg_holding_days.toFixed(1)}
            />
            <StatCard label="现金 Cash" value={money(perf.cash)} />
            <StatCard label="权益(按成本价)" value={money(perf.equity_at_cost)} />
          </div>
          <p className="text-xs text-slate-400">权益按成本价计,未含未实现浮盈亏。</p>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-slate-700">累计已实现盈亏</h2>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          {chartData.length === 0 ? (
            <p className="text-sm text-slate-500">还没有已平仓交易</p>
          ) : (
            <PriceChart data={chartData} />
          )}
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-slate-700">已平仓交易复盘</h2>
        {tradesError && <ErrorBanner message={tradesError} />}
        {trades && trades.length === 0 && !tradesError && (
          <p className="text-sm text-slate-500">还没有已平仓交易</p>
        )}
        {trades && trades.length > 0 && (
          <div className="overflow-x-auto rounded-md border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <Th>Symbol</Th>
                  <Th align="right">盈亏 %</Th>
                  <Th align="right">盈亏 $</Th>
                  <Th align="right">持有天数</Th>
                  <Th>复盘</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {trades.map((entry) => {
                  const evidence = parseEvidence(entry.evidence_json);
                  const snippet =
                    truncate(entry.title || "", REVIEW_SNIPPET_LEN) ||
                    truncate(entry.body || "", REVIEW_SNIPPET_LEN);
                  const full = [entry.title, entry.body].filter(Boolean).join("\n\n");
                  return (
                    <tr key={entry.id}>
                      <td className="px-3 py-2 font-medium">
                        {entry.symbol ? (
                          <Link
                            href={`/stock/${entry.symbol}`}
                            className="text-slate-900 underline decoration-slate-300 underline-offset-2 hover:text-indigo-700 hover:decoration-indigo-400"
                          >
                            {entry.symbol}
                          </Link>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                      <td
                        className={`px-3 py-2 text-right tabular-nums ${pnlColor(evidence.realized_pnl_pct)}`}
                      >
                        {signedPctPoints(evidence.realized_pnl_pct)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right tabular-nums ${pnlColor(evidence.realized_pnl)}`}
                      >
                        {signedMoney(evidence.realized_pnl)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {evidence.holding_days ?? "—"}
                      </td>
                      <td className="max-w-md truncate px-3 py-2 text-slate-600" title={full}>
                        {snippet || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-slate-700">最近决策</h2>
        {decisionsError && <ErrorBanner message={decisionsError} />}
        {decisions && decisions.length === 0 && !decisionsError && (
          <p className="text-sm text-slate-500">还没有决策记录</p>
        )}
        {decisions && decisions.length > 0 && (
          <div className="overflow-x-auto rounded-md border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <Th>As of</Th>
                  <Th>Symbol</Th>
                  <Th>Action</Th>
                  <Th align="right">置信度</Th>
                  <Th>Mode</Th>
                  <Th>主席裁决</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {decisions.map((d) => (
                  <tr key={d.id}>
                    <td className="whitespace-nowrap px-3 py-2 text-slate-500">{d.as_of}</td>
                    <td className="px-3 py-2 font-medium">
                      <Link
                        href={`/stock/${d.symbol}`}
                        className="text-slate-900 underline decoration-slate-300 underline-offset-2 hover:text-indigo-700 hover:decoration-indigo-400"
                      >
                        {d.symbol}
                      </Link>
                    </td>
                    <td className={`px-3 py-2 font-medium ${ACTION_COLORS[d.action] ?? "text-slate-500"}`}>
                      {d.action}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {(d.confidence * 100).toFixed(0)}%
                    </td>
                    <td className="px-3 py-2 text-slate-500">{d.mode}</td>
                    <td
                      className="max-w-md truncate px-3 py-2 text-slate-600"
                      title={d.chair_verdict}
                    >
                      {truncate(d.chair_verdict || "", CHAIR_VERDICT_SNIPPET_LEN) || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
