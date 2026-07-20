"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { OrderActionResponse, OrderResponse } from "@/lib/types";
import { ErrorBanner, Th } from "@/components/ui";

const POLL_MS = 15000;

export default function OrdersPage() {
  const [orders, setOrders] = useState<OrderResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<number, string>>({});
  const [rowBusy, setRowBusy] = useState<Record<number, boolean>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiGet<OrderResponse[]>("orders");
      setOrders(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load orders");
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  async function runAction(id: number, action: "approve" | "reject", body?: unknown) {
    setRowBusy((b) => ({ ...b, [id]: true }));
    setRowError((e) => ({ ...e, [id]: "" }));
    try {
      const res = await apiPost<OrderActionResponse>(`orders/${id}/${action}`, body);
      setNotice(`#${id}: ${res.note}`);
      await load();
    } catch (err) {
      let message = err instanceof Error ? err.message : "request failed";
      if (err instanceof ApiError) {
        if (err.status === 409) message = "订单已不在待确认状态";
        else if (err.status === 403) message = "缺少令牌 — 检查后端 .api_token";
      }
      setRowError((e) => ({ ...e, [id]: message }));
      await load();
    } finally {
      setRowBusy((b) => ({ ...b, [id]: false }));
    }
  }

  function onApprove(id: number) {
    if (!window.confirm(`确认批准订单 #${id}? 该操作将提交至模拟盘。`)) return;
    runAction(id, "approve");
  }

  function onReject(id: number) {
    const input = window.prompt("拒绝原因(可选,留空使用默认原因):", "");
    if (input === null) return; // cancelled — do nothing
    const reason = input.trim();
    runAction(id, "reject", reason ? { reason } : undefined);
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">待确认订单</h1>
        <p className="text-sm text-slate-500">
          semi_auto 模式下的人工确认闸门 — Approve / Reject 需要显式点击。
        </p>
      </div>

      {error && <ErrorBanner message={error} />}
      {notice && (
        <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-2 text-sm text-slate-700">
          {notice}
        </div>
      )}

      {orders && orders.length === 0 && !error && (
        <p className="text-sm text-slate-500">当前没有待确认订单</p>
      )}

      {orders && orders.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <Th>ID</Th>
                <Th>As of</Th>
                <Th>Side</Th>
                <Th>Symbol</Th>
                <Th align="right">Shares</Th>
                <Th>Status</Th>
                <Th>Actions</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {orders.map((o) => (
                <tr key={o.id}>
                  <td className="px-3 py-2 tabular-nums">{o.id}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{o.as_of}</td>
                  <td className="px-3 py-2 uppercase">
                    <span className={o.side === "buy" ? "text-emerald-700" : "text-red-700"}>
                      {o.side}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-medium text-slate-900">{o.symbol}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{o.shares}</td>
                  <td className="px-3 py-2">{o.status}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-col gap-1">
                      <div className="flex gap-2">
                        <button
                          onClick={() => onApprove(o.id)}
                          disabled={!!rowBusy[o.id]}
                          className="rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => onReject(o.id)}
                          disabled={!!rowBusy[o.id]}
                          className="rounded-md bg-red-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                        >
                          Reject
                        </button>
                      </div>
                      {rowError[o.id] && (
                        <span className="text-xs text-red-600">{rowError[o.id]}</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
