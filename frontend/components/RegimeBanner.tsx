"use client";

import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import { money } from "@/lib/format";
import type { MarketRegime } from "@/lib/types";

/** distance_pct is already a percentage-point value (SPY vs its 200-day SMA),
 * so format it directly — do NOT run it through signedPct (which ×100s). */
function signedPctPoints(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const sign = x > 0 ? "+" : "";
  return `${sign}${x.toFixed(digits)}%`;
}

/**
 * Self-contained market-regime banner (SPY vs its 200-day average). Fetches once
 * on mount — the regime is daily data, so there's nothing to gain from polling.
 * Context only: it never drives orders. Shared by the dashboard and the picks
 * page so the market backdrop shows up wherever recommendations are read.
 */
export function RegimeBanner({ caption = "仅市场背景参考,不驱动下单" }: { caption?: string }) {
  const [regime, setRegime] = useState<MarketRegime | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiGet<MarketRegime>("market/regime")
      .then((res) => {
        if (!cancelled) setRegime(res);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading && !regime) {
    return <p className="text-xs text-slate-400">大盘状态加载中…</p>;
  }
  // On fetch failure or when SPY data is unavailable, render nothing rather than
  // a misleading placeholder — this is background context, not a hard signal.
  if (failed || !regime || !regime.available) return null;

  return (
    <div
      className={
        "rounded-md border px-4 py-2 text-sm " +
        (regime.risk_on
          ? "border-emerald-300 bg-emerald-50 text-emerald-800"
          : "border-amber-300 bg-amber-50 text-amber-800")
      }
    >
      <p className="font-medium">
        {regime.risk_on
          ? `📈 大盘 risk-on — SPY ${money(regime.spy_close)} 在 200 日均线 ${money(regime.spy_sma200)} 上方 (${signedPctPoints(regime.distance_pct)})`
          : `⚠️ 大盘 risk-off — SPY ${money(regime.spy_close)} 跌破 200 日均线 ${money(regime.spy_sma200)} (${signedPctPoints(regime.distance_pct)})`}
      </p>
      <p className="mt-0.5 text-xs text-slate-500">{caption}</p>
    </div>
  );
}
