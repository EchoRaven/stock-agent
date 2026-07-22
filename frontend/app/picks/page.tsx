"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, apiPost } from "@/lib/api";
import type { PicksResponse } from "@/lib/types";
import { pct } from "@/lib/format";
import { ErrorBanner, Th } from "@/components/ui";
import { RegimeBanner } from "@/components/RegimeBanner";

const N_PICKS = 8;

// Picks are ephemeral server-side (generate_picks doesn't persist — persisting
// into the decisions table would skew the scorecard). Cache the last result in
// the browser so revisiting the page shows it instantly instead of a blank page
// plus a 1–2 min regenerate wait. Clearly timestamped so nobody mistakes a
// cached list for a fresh one.
const CACHE_KEY = "stock-agent:picks:last";

interface CachedPicks {
  result: PicksResponse;
  generatedAt: string; // ISO wall-clock, client-side
}

function todayLocalISODate(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

const ACTION_LABELS: Record<string, string> = { buy: "买入", sell: "卖出", hold: "观望" };

const ACTION_STYLES: Record<string, string> = {
  buy: "bg-emerald-100 text-emerald-800 border border-emerald-300",
  sell: "bg-red-100 text-red-800 border border-red-300",
  hold: "bg-slate-200 text-slate-700 border border-slate-300",
};

const CHAIR_VERDICT_SNIPPET_LEN = 140;

function truncate(text: string, max: number): string {
  const trimmed = text.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1)}…` : trimmed;
}

export default function PicksPage() {
  const [result, setResult] = useState<PicksResponse | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Hydrate the last cached result on mount (client-only — localStorage isn't
  // available during SSR). Corrupt/absent cache is ignored, never fatal.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as CachedPicks;
      if (parsed?.result?.picks) {
        setResult(parsed.result);
        setGeneratedAt(parsed.generatedAt ?? null);
      }
    } catch {
      /* ignore corrupt cache */
    }
  }, []);

  async function onGenerate() {
    setBusy(true);
    setError(null);
    try {
      const res = await apiPost<PicksResponse>("picks", { n: N_PICKS });
      const at = new Date().toISOString();
      setResult(res);
      setGeneratedAt(at);
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify({ result: res, generatedAt: at }));
      } catch {
        /* storage full/unavailable is non-fatal — the in-memory result still shows */
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 400) setError("Gemini 未配置");
        else if (err.status === 403) setError("缺少令牌");
        else setError(ApiError.detailToMessage(err.detail));
      } else {
        setError(err instanceof Error ? err.message : "生成荐股失败");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">Picks / 荐股</h1>
        <p className="mt-1 text-sm text-slate-500">
          对当日量化筛选出的候选,逐只跑 AI 委员会,按买入信心排序。这是分析建议,不会下单。
        </p>
      </div>

      {/* 大盘背景放在荐股前:回放评测证实委员会的买入倾向确实随 regime 变化
          (牛市多买、risk-off 少买),看荐股时先看市场环境有意义。 */}
      <RegimeBanner caption="大盘环境会影响委员会的买入倾向:risk-off 时通常更少建议买入。仅参考,不驱动下单。" />

      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={onGenerate}
          disabled={busy}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy ? "委员会分析中…" : result ? "重新生成" : "生成 AI 荐股"}
        </button>
        {busy && (
          <span className="text-sm text-slate-500">
            委员会分析中…每只一次 LLM 调用,可能要一两分钟
          </span>
        )}
      </div>

      {error && <ErrorBanner message={error} />}

      {result && (
        <div className="space-y-2">
          <p className="text-xs text-slate-400">
            as of {result.as_of} · {result.picks.length} 只候选 · {result.gemini_calls} 次 LLM 调用
            {generatedAt && ` · 生成于 ${new Date(generatedAt).toLocaleString()}`}
          </p>
          {/* 缓存的荐股来自更早的交易日 → 明确提示可能过时,别把旧列表当新的看 */}
          {result.as_of !== todayLocalISODate() && (
            <p className="text-xs text-amber-700">
              ⚠️ 这是较早的缓存结果(生成日 {result.as_of}),点上方「重新生成」可刷新为最新。
            </p>
          )}

          {result.errors.length > 0 && (
            <p className="text-sm text-slate-500">{result.errors.length} 只分析失败</p>
          )}

          {result.picks.length === 0 ? (
            <p className="text-sm text-slate-500">无候选(先跑量化筛选)</p>
          ) : (
            <div className="overflow-x-auto rounded-md border border-slate-200">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50">
                  <tr>
                    <Th>Rank</Th>
                    <Th>Symbol</Th>
                    <Th>Action</Th>
                    <Th align="right">置信度</Th>
                    <Th align="right">Quant Score</Th>
                    <Th>Chair Verdict</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {result.picks.map((p) => (
                    <tr
                      key={p.symbol}
                      className={p.action === "buy" ? "bg-emerald-50/50" : undefined}
                    >
                      <td className="px-3 py-2 tabular-nums">{p.rank}</td>
                      <td className="px-3 py-2 font-medium">
                        <Link
                          href={`/stock/${p.symbol}`}
                          className="text-slate-900 underline decoration-slate-300 underline-offset-2 hover:text-indigo-700 hover:decoration-indigo-400"
                        >
                          {p.symbol}
                        </Link>
                        {p.held && (
                          <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">
                            持有
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                            ACTION_STYLES[p.action] ?? ACTION_STYLES.hold
                          }`}
                        >
                          {ACTION_LABELS[p.action] ?? p.action}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {pct(p.confidence, 0)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {p.quant_score.toFixed(4)}
                      </td>
                      <td
                        className="max-w-md truncate px-3 py-2 text-slate-600"
                        title={p.chair_verdict}
                      >
                        {truncate(p.chair_verdict || "", CHAIR_VERDICT_SNIPPET_LEN) || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
