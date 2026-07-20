"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, apiDelete, apiGet, apiPost } from "@/lib/api";
import type { WatchlistItem } from "@/lib/types";
import { ErrorBanner, Th } from "@/components/ui";

const POLL_MS = 15000;

function fmtNum(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return x.toFixed(digits);
}

/** change_pct is already in percentage-point units (e.g. 1.5 == +1.5%). */
function fmtChange(change: number | null, changePct: number | null): string {
  if (change === null || changePct === null) return "—";
  const sign = change > 0 ? "+" : "";
  return `${sign}${change.toFixed(2)} (${sign}${changePct.toFixed(2)}%)`;
}

function changeColor(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "text-slate-500";
  return x > 0 ? "text-emerald-700" : x < 0 ? "text-red-700" : "text-slate-500";
}

function errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "缺少令牌";
    return ApiError.detailToMessage(err.detail);
  }
  return err instanceof Error ? err.message : fallback;
}

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [rowBusy, setRowBusy] = useState<Record<string, boolean>>({});
  const [rowError, setRowError] = useState<Record<string, string>>({});

  const [newSymbol, setNewSymbol] = useState("");
  const [newNote, setNewNote] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiGet<WatchlistItem[]>("watchlist");
      setItems(res);
      setError(null);
    } catch (err) {
      setError(errMessage(err, "自选列表加载失败"));
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  async function onAdd() {
    const symbol = newSymbol.trim().toUpperCase();
    if (!symbol) return;
    const note = newNote.trim();
    setAddBusy(true);
    setAddError(null);
    try {
      await apiPost("watchlist", note ? { symbol, note } : { symbol });
      setNewSymbol("");
      setNewNote("");
      await load();
    } catch (err) {
      setAddError(errMessage(err, "添加失败"));
    } finally {
      setAddBusy(false);
    }
  }

  async function onDelete(symbol: string) {
    setRowBusy((b) => ({ ...b, [symbol]: true }));
    setRowError((e) => ({ ...e, [symbol]: "" }));
    try {
      await apiDelete(`watchlist/${symbol}`);
      await load();
    } catch (err) {
      setRowError((e) => ({ ...e, [symbol]: errMessage(err, "删除失败") }));
      setRowBusy((b) => ({ ...b, [symbol]: false }));
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">自选 / Watchlist</h1>
        <p className="text-sm text-slate-500">关注的股票与实时报价,每 15 秒刷新。</p>
      </div>

      {error && <ErrorBanner message={error} />}

      {items && items.length === 0 && !error && (
        <p className="text-sm text-slate-500">自选为空,在下方或个股页添加</p>
      )}

      {items && items.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <Th>Symbol</Th>
                <Th align="right">现价</Th>
                <Th align="right">涨跌</Th>
                <Th>备注</Th>
                <Th>操作</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((it) => (
                <tr key={it.symbol}>
                  <td className="px-3 py-2 font-medium">
                    <Link
                      href={`/stock/${it.symbol}`}
                      className="text-slate-900 underline decoration-slate-300 underline-offset-2 hover:text-indigo-700 hover:decoration-indigo-400"
                    >
                      {it.symbol}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {fmtNum(it.current_price)}
                  </td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums ${changeColor(it.change)}`}
                  >
                    {fmtChange(it.change, it.change_pct)}
                  </td>
                  <td className="max-w-xs truncate px-3 py-2 text-slate-600" title={it.note ?? ""}>
                    {it.note || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-col gap-1">
                      <button
                        onClick={() => onDelete(it.symbol)}
                        disabled={!!rowBusy[it.symbol]}
                        className="w-fit rounded-md bg-red-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                      >
                        删除
                      </button>
                      {rowError[it.symbol] && (
                        <span className="text-xs text-red-600">{rowError[it.symbol]}</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <section className="rounded-md border border-slate-200 bg-white p-4">
        <h2 className="mb-2 text-sm font-semibold text-slate-700">添加自选</h2>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">代码</span>
            <input
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onAdd();
              }}
              placeholder="如 AAPL"
              className="input w-32"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">备注(可选)</span>
            <input
              value={newNote}
              onChange={(e) => setNewNote(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onAdd();
              }}
              placeholder="备注"
              className="input w-48"
            />
          </label>
          <button
            onClick={onAdd}
            disabled={addBusy || !newSymbol.trim()}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {addBusy ? "添加中…" : "添加"}
          </button>
        </div>
        {addError && <p className="mt-2 text-sm text-red-600">{addError}</p>}
      </section>
    </div>
  );
}
