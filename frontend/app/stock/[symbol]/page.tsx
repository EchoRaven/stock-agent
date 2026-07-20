"use client";

import { useEffect, useState } from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { CommitteeRoleKey, FundamentalPoint, StockAnalysis, StockDetail } from "@/lib/types";
import { ErrorBanner, StatCard } from "@/components/ui";
import { PriceChart } from "@/components/PriceChart";

function fmtNum(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return x.toFixed(digits);
}

/** summary.pct_* fields are already in percentage-point units (e.g. 1.23 == +1.23%). */
function fmtPct(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const sign = x > 0 ? "+" : "";
  return `${sign}${x.toFixed(digits)}%`;
}

function fmtVol(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return Math.round(x).toLocaleString("en-US");
}

function changeColor(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "text-slate-500";
  return x > 0 ? "text-emerald-700" : x < 0 ? "text-red-700" : "text-slate-500";
}

const ACTION_LABELS: Record<string, string> = { buy: "买入", sell: "卖出", hold: "观望" };

const ACTION_STYLES: Record<string, string> = {
  buy: "bg-emerald-100 text-emerald-800 border border-emerald-300",
  sell: "bg-red-100 text-red-800 border border-red-300",
  hold: "bg-slate-200 text-slate-700 border border-slate-300",
};

const ROLE_LABELS: Record<CommitteeRoleKey, string> = {
  technical: "技术面",
  fundamental: "基本面",
  sentiment: "情绪面",
  bear: "空头",
};

const ROLE_ORDER: CommitteeRoleKey[] = ["technical", "fundamental", "sentiment", "bear"];

function FundamentalTable({
  title,
  points,
  digits = 0,
}: {
  title: string;
  points: FundamentalPoint[];
  digits?: number;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </div>
      {points.length === 0 ? (
        <p className="text-xs text-slate-400">—</p>
      ) : (
        <table className="w-full text-xs">
          <tbody className="divide-y divide-slate-100">
            {points.map((p) => (
              <tr key={p.end}>
                <td className="py-1 pr-2 text-slate-500">{p.fiscal}</td>
                <td className="py-1 text-right tabular-nums text-slate-900">
                  {p.value.toLocaleString("en-US", {
                    maximumFractionDigits: digits,
                    minimumFractionDigits: digits,
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function StockDetailPage({ params }: { params: { symbol: string } }) {
  const symbol = (params.symbol || "").trim().toUpperCase();

  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);

  const [analysis, setAnalysis] = useState<StockAnalysis | null>(null);
  const [analyzeBusy, setAnalyzeBusy] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotFound(false);
    setDetail(null);
    setAnalysis(null);
    setAnalyzeError(null);
    apiGet<StockDetail>(`stock/${symbol}?days=365`)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : "加载股票详情失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  async function onAnalyze() {
    setAnalyzeBusy(true);
    setAnalyzeError(null);
    try {
      const res = await apiPost<StockAnalysis>(`stock/${symbol}/analyze`, {});
      setAnalysis(res);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 400) setAnalyzeError("Gemini 未配置");
        else if (err.status === 403) setAnalyzeError("缺少令牌");
        else setAnalyzeError(ApiError.detailToMessage(err.detail));
      } else {
        setAnalyzeError(err instanceof Error ? err.message : "分析失败");
      }
    } finally {
      setAnalyzeBusy(false);
    }
  }

  if (!symbol) return <ErrorBanner message="缺少股票代码" />;
  if (loading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (notFound) return <ErrorBanner message={`查无此代码或无价格数据: ${symbol}`} />;
  if (error) return <ErrorBanner message={error} />;
  if (!detail) return null;

  const { summary } = detail;
  const hasFundamentals =
    detail.fundamentals.revenue.length > 0 ||
    detail.fundamentals.net_income.length > 0 ||
    detail.fundamentals.eps.length > 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{detail.symbol}</h1>
          <span className="text-xl font-semibold tabular-nums text-slate-900">
            {fmtNum(summary.last_close)}
          </span>
          <span className={`text-sm font-medium tabular-nums ${changeColor(summary.chg_1d)}`}>
            {fmtPct(summary.pct_1d)} 今日
          </span>
        </div>
        <p className="text-xs text-slate-400">as of {detail.as_of}</p>
      </div>

      <section className="rounded-md border border-slate-200 bg-white p-4">
        <PriceChart data={detail.price_series} />
      </section>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="1D" value={fmtPct(summary.pct_1d)} />
        <StatCard label="5D" value={fmtPct(summary.pct_5d)} />
        <StatCard label="20D" value={fmtPct(summary.pct_20d)} />
        <StatCard label="52W High" value={fmtNum(summary.high_52w)} />
        <StatCard label="52W Low" value={fmtNum(summary.low_52w)} />
        <StatCard label="SMA20" value={fmtNum(summary.sma20)} />
        <StatCard label="SMA50" value={fmtNum(summary.sma50)} />
        <StatCard label="RSI14" value={fmtNum(summary.rsi14)} />
        <StatCard label="Avg Vol 20" value={fmtVol(summary.avg_vol_20)} />
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">近期新闻</h2>
        {detail.news.length === 0 ? (
          <p className="text-sm text-slate-500">无近期新闻</p>
        ) : (
          <ul className="space-y-3">
            {detail.news.map((n, i) => (
              <li key={`${n.url}-${i}`} className="rounded-md border border-slate-200 bg-white p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span>{n.date}</span>
                  <span>·</span>
                  <span>{n.source}</span>
                </div>
                <a
                  href={n.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 block text-sm font-medium text-slate-900 hover:text-indigo-700 hover:underline"
                >
                  {n.headline}
                </a>
                {n.summary && <p className="mt-1 text-xs text-slate-500">{n.summary}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">基本面(EDGAR)</h2>
        {!hasFundamentals ? (
          <p className="text-sm text-slate-500">无基本面数据(EDGAR)</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-3">
            <FundamentalTable title="Revenue" points={detail.fundamentals.revenue} />
            <FundamentalTable title="Net income" points={detail.fundamentals.net_income} />
            <FundamentalTable title="EPS" points={detail.fundamentals.eps} digits={2} />
          </div>
        )}
      </section>

      <section className="rounded-md border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-700">AI 委员会分析</h2>
          <button
            onClick={onAnalyze}
            disabled={analyzeBusy}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {analyzeBusy ? "委员会分析中…一次 LLM 调用" : "跑 AI 委员会分析"}
          </button>
        </div>
        {analyzeError && <p className="mt-2 text-sm text-red-600">{analyzeError}</p>}
        {analysis && (
          <div className="mt-4 space-y-4">
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                分析建议(仅分析,不会下单)
              </span>
              <span
                className={`rounded-full px-3 py-1 text-sm font-semibold ${
                  ACTION_STYLES[analysis.action] ?? ACTION_STYLES.hold
                }`}
              >
                {ACTION_LABELS[analysis.action] ?? analysis.action}
              </span>
              <span className="text-sm tabular-nums text-slate-700">
                置信度 {(analysis.confidence * 100).toFixed(0)}%
              </span>
              {analysis.held && (
                <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">
                  当前持有
                </span>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              {ROLE_ORDER.map((role) => (
                <div key={role} className="rounded-md border border-slate-200 bg-white p-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {ROLE_LABELS[role]}
                  </div>
                  <p className="mt-1 text-sm text-slate-700">{analysis.committee[role]?.summary}</p>
                </div>
              ))}
            </div>

            <div className="rounded-md border border-slate-200 bg-white p-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                主席裁决
              </div>
              <p className="mt-1 text-sm text-slate-700">{analysis.chair.verdict}</p>
              <div className="mt-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                对空头的回应
              </div>
              <p className="mt-1 text-sm text-slate-700">{analysis.chair.bear_rebuttal}</p>
            </div>

            <p className="text-xs text-slate-400">
              {analysis.note} · as of {analysis.as_of}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
