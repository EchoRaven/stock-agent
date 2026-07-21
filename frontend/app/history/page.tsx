"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, apiGet } from "@/lib/api";
import { money, pct } from "@/lib/format";
import type {
  DecisionHistoryItem,
  ForwardReturns,
  MemoryEntry,
  PerformanceResponse,
  Scorecard,
  ScorecardFlag,
} from "@/lib/types";
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

const ACTION_LABELS: Record<string, string> = { buy: "买入", sell: "卖出", hold: "观望" };

const ACTION_BAR_COLORS: Record<string, string> = {
  buy: "bg-emerald-500",
  sell: "bg-red-500",
  hold: "bg-slate-400",
};

const FLAG_CHIP_STYLES: Record<ScorecardFlag["severity"], string> = {
  warn: "border border-amber-300 bg-amber-50 text-amber-800",
  info: "border border-slate-200 bg-slate-100 text-slate-600",
};

function errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return ApiError.detailToMessage(err.detail);
  return err instanceof Error ? err.message : fallback;
}

/** Track + fill bar shared by the action-mix and confidence-histogram rows.
 * `widthPct` is pre-clamped by the caller (proportion of total for action mix,
 * proportion of the tallest bucket for the histogram). */
function BarRow({
  label,
  detail,
  widthPct,
  barClassName,
}: {
  label: string;
  detail: string;
  widthPct: number;
  barClassName: string;
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-14 shrink-0 text-slate-500">{label}</span>
      <div className="h-2 flex-1 rounded-full bg-slate-100">
        <div
          className={`h-2 rounded-full ${barClassName}`}
          style={{ width: `${Math.max(0, Math.min(100, widthPct))}%` }}
        />
      </div>
      <span className="w-16 shrink-0 text-right tabular-nums text-slate-600">{detail}</span>
    </div>
  );
}

export default function HistoryPage() {
  const [perf, setPerf] = useState<PerformanceResponse | null>(null);
  const [perfError, setPerfError] = useState<string | null>(null);

  const [trades, setTrades] = useState<MemoryEntry[] | null>(null);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const [decisions, setDecisions] = useState<DecisionHistoryItem[] | null>(null);
  const [decisionsError, setDecisionsError] = useState<string | null>(null);

  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [scorecardError, setScorecardError] = useState<string | null>(null);

  const [forward, setForward] = useState<ForwardReturns | null>(null);
  const [forwardError, setForwardError] = useState<string | null>(null);

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
      // Scorecard is best-effort: the rest of the history page (performance,
      // trade reviews, decisions table) must still render if this fails.
      apiGet<Scorecard>("decisions/scorecard")
        .then((res) => {
          if (!cancelled) {
            setScorecard(res);
            setScorecardError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setScorecardError(errMessage(err, "决策记分卡加载失败"));
        }),
      // Forward returns hit the price provider, so they are the slowest and
      // most failure-prone call on this page — also strictly best-effort.
      apiGet<ForwardReturns>("decisions/forward-returns")
        .then((res) => {
          if (!cancelled) {
            setForward(res);
            setForwardError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setForwardError(errMessage(err, "前瞻收益加载失败"));
        }),
    ]).finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  if (loading && !perf && !trades && !decisions && !scorecard) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }

  const chartData = (perf?.cumulative_pnl_series ?? []).map((p) => ({
    date: p.date,
    close: p.cum_pnl,
  }));

  // "insufficient" (or empty) data means the only flag is insufficient_data —
  // mirrors the backend's MIN_FOR_FLAGS gate (app/services/scorecard_service.py).
  // In that case we show the flag message but skip the bars (nothing meaningful
  // to draw off <10 decisions).
  const scorecardInsufficient =
    scorecard?.flags.some((f) => f.code === "insufficient_data") ?? true;
  const maxHistCount = scorecard
    ? Math.max(1, ...scorecard.histogram.map((b) => b.count))
    : 1;

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
        <h2 className="text-sm font-semibold text-slate-700">决策记分卡</h2>
        <p className="text-xs text-slate-500">
          委员会的推荐是否有区分度——动作分布是否偏买、置信度是否压缩在一小段区间。
        </p>
        {scorecardError && <ErrorBanner message={scorecardError} />}
        {scorecard && (
          <div className="space-y-4 rounded-md border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm text-slate-600">
              <span>
                样本{" "}
                <span className="font-semibold tabular-nums text-slate-900">
                  {scorecard.total}
                </span>{" "}
                条
              </span>
              <span>
                窗口{" "}
                {scorecard.window_days === null ? "全部历史" : `近 ${scorecard.window_days} 天`}
              </span>
              {scorecard.as_of_from && scorecard.as_of_to && (
                <span className="text-slate-400">
                  {scorecard.as_of_from} 至 {scorecard.as_of_to}
                </span>
              )}
              <span className="text-slate-400">{scorecard.distinct_symbols} 只标的</span>
            </div>

            <div className="flex flex-wrap gap-2">
              {scorecard.flags.map((flag) => (
                <span
                  key={flag.code}
                  className={`rounded-full px-2.5 py-1 text-xs font-medium ${FLAG_CHIP_STYLES[flag.severity]}`}
                >
                  {flag.message}
                </span>
              ))}
            </div>

            {!scorecardInsufficient && (
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
                    动作分布
                  </div>
                  {(["buy", "sell", "hold"] as const).map((action) => (
                    <BarRow
                      key={action}
                      label={ACTION_LABELS[action]}
                      detail={`${scorecard.by_action[action]} (${pct(scorecard.by_action_pct[action], 1)})`}
                      widthPct={scorecard.by_action_pct[action] * 100}
                      barClassName={ACTION_BAR_COLORS[action]}
                    />
                  ))}
                </div>

                <div className="space-y-1.5">
                  <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
                    置信度分布
                  </div>
                  {scorecard.histogram.map((b) => (
                    <BarRow
                      key={b.bucket}
                      label={b.bucket}
                      detail={String(b.count)}
                      widthPct={(b.count / maxHistCount) * 100}
                      barClassName="bg-indigo-400"
                    />
                  ))}
                  <p className="pt-1 text-xs text-slate-500">
                    均值{" "}
                    {scorecard.confidence.mean === null ? "—" : scorecard.confidence.mean.toFixed(3)}{" "}
                    · 中位数{" "}
                    {scorecard.confidence.median === null
                      ? "—"
                      : scorecard.confidence.median.toFixed(3)}{" "}
                    · 标准差{" "}
                    {scorecard.confidence.stdev === null
                      ? "—"
                      : scorecard.confidence.stdev.toFixed(3)}
                  </p>
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-slate-700">决策是否奏效(前瞻收益)</h2>
        <p className="text-xs text-slate-500">
          决策做出之后股价实际怎么走——按动作、按置信度分桶。核心问题:高置信度是否真的对应更好的收益。
        </p>
        {forwardError && <ErrorBanner message={forwardError} />}
        {forward && (
          <div className="space-y-4 rounded-md border border-slate-200 bg-white p-4">
            <p className="text-sm text-slate-600">{forward.note}</p>

            {forward.horizons.map((h) => {
              const block = forward.by_horizon[String(h)];
              if (!block) return null;
              const { matured, pending, unpriced } = block.coverage;
              const signal = block.confidence_signal;
              const maxBucketN = Math.max(1, ...block.buy_by_confidence.map((b) => b.n));

              return (
                <div
                  key={h}
                  className="space-y-2 border-t border-slate-100 pt-3 first:border-t-0 first:pt-0"
                >
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="text-sm font-semibold text-slate-900">{h} 个交易日后</span>
                    <span className="text-xs text-slate-400">
                      已成熟 {matured} · 未到期 {pending} · 无行情 {unpriced}
                    </span>
                  </div>

                  {/* matured === 0 renders the coverage explanation, never a table of
                      dashes — "not known yet" must not look like "measured as zero". */}
                  {matured === 0 ? (
                    <p className="text-xs text-slate-500">
                      尚无成熟数据{pending > 0 && `:${pending} 条决策还没走满 ${h} 个交易日`}
                      {unpriced > 0 && `,${unpriced} 条拿不到行情`},暂时无法评价。
                    </p>
                  ) : (
                    <>
                      <div className="overflow-x-auto">
                        <table className="min-w-full text-sm">
                          <thead className="bg-slate-50">
                            <tr>
                              <Th>动作</Th>
                              <Th align="right">样本</Th>
                              <Th align="right">平均收益</Th>
                              <Th align="right">中位数</Th>
                              <Th align="right">命中率</Th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100">
                            {(["buy", "sell", "hold"] as const).map((action) => {
                              const s = block.by_action[action];
                              return (
                                <tr key={action}>
                                  <td className={`px-3 py-1.5 font-medium ${ACTION_COLORS[action]}`}>
                                    {ACTION_LABELS[action]}
                                  </td>
                                  <td className="px-3 py-1.5 text-right tabular-nums text-slate-600">
                                    {s.n}
                                  </td>
                                  <td
                                    className={`px-3 py-1.5 text-right tabular-nums ${pnlColor(s.mean_return_pct)}`}
                                  >
                                    {signedPctPoints(s.mean_return_pct, 2)}
                                  </td>
                                  <td
                                    className={`px-3 py-1.5 text-right tabular-nums ${pnlColor(s.median_return_pct)}`}
                                  >
                                    {signedPctPoints(s.median_return_pct, 2)}
                                  </td>
                                  <td
                                    className="px-3 py-1.5 text-right tabular-nums text-slate-600"
                                    title={s.hit_rate_meaning}
                                  >
                                    {s.hit_rate === null ? "—" : pct(s.hit_rate, 0)}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>

                      <div className="space-y-1.5">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
                          买入决策 · 按置信度分桶
                        </div>
                        {block.buy_by_confidence.map((b) => (
                          <BarRow
                            key={b.bucket}
                            label={b.bucket}
                            detail={
                              b.n === 0
                                ? "—"
                                : `${b.n} 条 · ${signedPctPoints(b.mean_return_pct, 2)} · 命中 ${
                                    b.hit_rate === null ? "—" : pct(b.hit_rate, 0)
                                  }`
                            }
                            widthPct={(b.n / maxBucketN) * 100}
                            barClassName="bg-indigo-400"
                          />
                        ))}
                      </div>

                      <p className="pt-1 text-xs">
                        {signal.verdict ? (
                          <span className="font-medium text-slate-700">{signal.verdict}</span>
                        ) : (
                          <span className="text-slate-500">{signal.note}</span>
                        )}
                        <span className="ml-1 text-slate-400">
                          ({signal.n} 条买入 / {signal.distinct_days} 个决策日
                          {signal.significant === false && " · 未通过显著性检验"})
                        </span>
                      </p>
                      {signal.caveat && (
                        <p className="text-xs text-amber-700">⚠ {signal.caveat}</p>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

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
