"use client";

import { useState } from "react";

import { ApiError, apiPost } from "@/lib/api";
import type { SentimentResponse } from "@/lib/types";
import { Field } from "@/components/ui";

function scoreColor(score: number): string {
  if (score > 0.15) return "text-emerald-700";
  if (score < -0.15) return "text-red-700";
  return "text-slate-600";
}

export function SentimentWidget() {
  const [symbol, setSymbol] = useState("AAPL");
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SentimentResponse | null>(null);

  async function onLookup() {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiPost<SentimentResponse>("sentiment", { symbol: sym, days });
      setResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? ApiError.detailToMessage(err.detail) : "情绪查询失败");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="rounded-md border border-slate-200 bg-white p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-700">新闻情绪查询</h2>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Symbol">
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="input w-28 uppercase"
          />
        </Field>
        <Field label="Days">
          <input
            type="number"
            min={1}
            max={90}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="input w-20"
          />
        </Field>
        <button
          onClick={onLookup}
          disabled={loading || !symbol.trim()}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {loading ? "查询中…" : "查询情绪"}
        </button>
      </div>

      {loading && (
        <p className="mt-3 text-sm text-slate-500">查询中(需拉取新闻 + LLM 打分,可能需要几秒)…</p>
      )}
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {!loading && result && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <span className="font-medium text-slate-900">{result.symbol}</span>
            <span className="text-slate-500">as of {result.as_of}</span>
            <span className="tabular-nums text-slate-500">news: {result.news_count}</span>
            {result.scored && result.sentiment !== null ? (
              <span className={`font-semibold tabular-nums ${scoreColor(result.sentiment)}`}>
                sentiment: {result.sentiment.toFixed(3)}
              </span>
            ) : (
              <span className="text-slate-400">未打分(无 Gemini key 或无新闻)</span>
            )}
          </div>

          {result.headlines.length > 0 ? (
            <div className="max-h-64 overflow-y-auto rounded-md border border-slate-200">
              <ul className="divide-y divide-slate-100 text-sm">
                {result.headlines.map((h, i) => (
                  <li key={i} className="px-3 py-2">
                    <span className="text-slate-400">{h.date}</span>{" "}
                    <span className="text-slate-400">· {h.source} ·</span>{" "}
                    <span className="text-slate-800">{h.headline}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-slate-500">该时间窗口内无新闻</p>
          )}
        </div>
      )}
    </section>
  );
}
