"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import { money } from "@/lib/format";
import type {
  DashboardResponse,
  SettleResponse,
  TradeCycleResponse,
  WatchdogResponse,
} from "@/lib/types";
import { ErrorBanner, StatCard, Th } from "@/components/ui";

const POLL_MS = 15000;

function tokenAwareMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "缺少令牌 — 检查后端 .api_token";
    return ApiError.detailToMessage(err.detail);
  }
  return err instanceof Error ? err.message : fallback;
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [settleBusy, setSettleBusy] = useState(false);
  const [settleError, setSettleError] = useState<string | null>(null);
  const [settleNotice, setSettleNotice] = useState<string | null>(null);

  const [watchdogBusy, setWatchdogBusy] = useState(false);
  const [watchdogError, setWatchdogError] = useState<string | null>(null);
  const [watchdogNotice, setWatchdogNotice] = useState<string | null>(null);

  const [cycleBusy, setCycleBusy] = useState(false);
  const [cycleError, setCycleError] = useState<string | null>(null);
  const [cycleResult, setCycleResult] = useState<TradeCycleResponse | null>(null);

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

  const maintenanceBusy = settleBusy || watchdogBusy;

  async function onSettle() {
    if (!window.confirm("确认撮合所有待成交单?该操作将执行成交(写入 fills、更新持仓)。"))
      return;
    setSettleBusy(true);
    setSettleError(null);
    setSettleNotice(null);
    try {
      const res = await apiPost<SettleResponse>("orders/settle", {});
      setSettleNotice(`已撮合 ${res.count} 笔成交`);
      await load();
    } catch (err) {
      setSettleError(tokenAwareMessage(err, "撮合失败"));
    } finally {
      setSettleBusy(false);
    }
  }

  async function onWatchdog() {
    setWatchdogBusy(true);
    setWatchdogError(null);
    setWatchdogNotice(null);
    try {
      const res = await apiPost<WatchdogResponse>("watchdog", {});
      setWatchdogNotice(
        res.downgraded
          ? `不健康 — 模式已从 ${res.mode_before} 降级为 ${res.mode_after}: ${res.reasons.join("; ")}`
          : res.healthy
            ? "健康"
            : `不健康(未降级): ${res.reasons.join("; ")}`
      );
      await load();
    } catch (err) {
      setWatchdogError(tokenAwareMessage(err, "watchdog 运行失败"));
    } finally {
      setWatchdogBusy(false);
    }
  }

  async function onRunCycle() {
    if (
      !window.confirm(
        "跑一轮 AI 委员会交易?会对筛选出的候选逐只调用 Gemini 委员会做决策。" +
          "只有当前 mode=full_auto 时才会真的在模拟盘下单成交(仍走风控闸门)。"
      )
    )
      return;
    setCycleBusy(true);
    setCycleError(null);
    setCycleResult(null);
    try {
      const res = await apiPost<TradeCycleResponse>("trade/cycle", {});
      setCycleResult(res);
      await load();
    } catch (err) {
      setCycleError(tokenAwareMessage(err, "委员会交易运行失败"));
    } finally {
      setCycleBusy(false);
    }
  }

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

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">AI 委员会交易(模拟盘)</h2>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="mb-3 text-xs text-slate-500">
            对筛选出的候选逐只跑 Gemini 四角色委员会(技术/基本面/情绪/空头 + 主席裁决),
            决策经风控闸门后
            {data.mode === "full_auto" ? (
              <span className="font-medium text-red-700"> 自动在模拟盘成交</span>
            ) : (
              <span className="font-medium text-amber-700">
                {" "}
                仅记录/挂待确认(当前 mode={data.mode},非 full_auto 不自动成交)
              </span>
            )}
            。
          </p>
          <button
            onClick={onRunCycle}
            disabled={cycleBusy || maintenanceBusy}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {cycleBusy ? "委员会分析中…(每只股票一次 LLM 调用,可能要一两分钟)" : "跑一轮 AI 委员会交易"}
          </button>
          {cycleError && <p className="mt-2 text-sm text-red-600">cycle: {cycleError}</p>}
          {cycleResult && (
            <div className="mt-4 space-y-3">
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-600">
                <span>as of {cycleResult.as_of}</span>
                <span>mode {cycleResult.mode}</span>
                <span>评估 {cycleResult.evaluated} 只</span>
                <span>LLM 调用 {cycleResult.gemini_calls}</span>
                <span>成交 {cycleResult.fills.length}</span>
                {cycleResult.errors.length > 0 && (
                  <span className="text-red-600">错误 {cycleResult.errors.length}</span>
                )}
              </div>
              {cycleResult.decisions.length > 0 && (
                <div className="overflow-x-auto rounded-md border border-slate-200">
                  <table className="min-w-full divide-y divide-slate-200 text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        <Th>Symbol</Th>
                        <Th>Action</Th>
                        <Th align="right">Conf.</Th>
                        <Th align="right">Shares</Th>
                        <Th>结果</Th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {cycleResult.decisions.map((d) => (
                        <tr key={d.symbol}>
                          <td className="px-3 py-2 font-medium text-slate-900">{d.symbol}</td>
                          <td
                            className={
                              "px-3 py-2 font-medium " +
                              (d.action === "buy"
                                ? "text-emerald-700"
                                : d.action === "sell"
                                  ? "text-red-700"
                                  : "text-slate-500")
                            }
                          >
                            {d.action}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {d.confidence.toFixed(2)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {d.shares ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-xs text-slate-500">
                            {d.submit_result?.note ?? d.submit_result?.status ?? ""}
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
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">维护操作</h2>
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-white p-4">
          <button
            onClick={onSettle}
            disabled={maintenanceBusy}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {settleBusy ? "撮合中…" : "撮合待成交单 (settle)"}
          </button>
          <button
            onClick={onWatchdog}
            disabled={maintenanceBusy}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {watchdogBusy ? "运行中…" : "运行 watchdog"}
          </button>
        </div>
        {settleNotice && <p className="mt-2 text-sm text-emerald-700">{settleNotice}</p>}
        {settleError && <p className="mt-2 text-sm text-red-600">settle: {settleError}</p>}
        {watchdogNotice && <p className="mt-2 text-sm text-slate-700">{watchdogNotice}</p>}
        {watchdogError && <p className="mt-2 text-sm text-red-600">watchdog: {watchdogError}</p>}
      </section>

      <p className="text-xs text-slate-400">as of {data.as_of}</p>
    </div>
  );
}
