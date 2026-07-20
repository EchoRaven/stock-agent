"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { ExecutionBackend, ExecutionResponse } from "@/lib/types";
import { ErrorBanner, StatCard } from "@/components/ui";

const POLL_MS = 15000;

const BACKEND_LABELS: Record<ExecutionBackend, string> = {
  paper: "Paper(模拟盘,内部撮合)",
  futu_paper: "Futu Paper(模拟盘,经 OpenD 网关)",
};

export default function ExecutionPage() {
  const [data, setData] = useState<ExecutionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [switchBusy, setSwitchBusy] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiGet<ExecutionResponse>("execution");
      setData(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load execution state");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  async function switchBackend(backend: ExecutionBackend) {
    if (!data || backend === data.backend) return;
    setSwitchBusy(true);
    setSwitchError(null);
    try {
      const res = await apiPost<ExecutionResponse>("execution/backend", { backend });
      setData(res);
    } catch (err) {
      setSwitchError(
        err instanceof ApiError ? ApiError.detailToMessage(err.detail) : "切换执行后端失败"
      );
    } finally {
      setSwitchBusy(false);
    }
  }

  if (loading && !data) return <p className="text-sm text-slate-500">Loading…</p>;
  if (error && !data) return <ErrorBanner message={error} />;
  if (!data) return null;

  const { futu } = data;

  return (
    <div className="space-y-6">
      {error && <ErrorBanner message={error} />}

      <section>
        <h2 className="mb-1 text-sm font-semibold text-slate-700">执行后端(Execution backend)</h2>
        <p className="mb-3 text-xs text-slate-500">
          当前:
          <span className="ml-1 font-medium text-slate-900">
            {BACKEND_LABELS[data.backend] ?? data.backend}
          </span>
        </p>
        <div className="flex flex-wrap gap-3">
          {data.available_backends.map((b) => (
            <button
              key={b}
              onClick={() => switchBackend(b)}
              disabled={switchBusy || b === data.backend}
              className={`rounded-md border px-4 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                data.backend === b
                  ? "border-slate-400 bg-slate-100 text-slate-900"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-400"
              }`}
            >
              <div className="font-medium">{b}</div>
              <div className="text-xs opacity-80">{BACKEND_LABELS[b] ?? ""}</div>
            </button>
          ))}
        </div>
        {switchError && <p className="mt-2 text-sm text-red-600">{switchError}</p>}

        {data.backend === "futu_paper" && !futu.opend_reachable && (
          <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            OpenD 网关未连接 — futu_paper 下单将失败,需先在 {futu.host}:{futu.port} 启动 OpenD。
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Futu 状态</h2>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="Host" value={futu.host} />
          <StatCard label="Port" value={String(futu.port)} />
          <StatCard label="Trd env" value={futu.trd_env} />
          <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
              OpenD 连接
            </div>
            <div className="mt-1 text-lg font-semibold">
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  futu.opend_reachable
                    ? "bg-emerald-100 text-emerald-800 border border-emerald-300"
                    : "bg-slate-200 text-slate-500"
                }`}
              >
                {futu.opend_reachable ? "已连接" : "未连接 — 需启动 OpenD 网关"}
              </span>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-md border border-red-300 bg-red-50 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-red-800">真钱(REAL)不可从界面开启</p>
            <p className="mt-1 text-xs text-red-700">
              需在服务器 env 设置 <code className="rounded bg-red-100 px-1">STOCKAGENT_FUTU_ALLOW_REAL</code>{" "}
              + 解锁密码,且默认只走模拟盘。此页面没有、也永远不会有任何能触达真实资金的控件。
            </p>
          </div>
          <span
            className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold uppercase tracking-wide ${
              futu.allow_real
                ? "border border-red-400 bg-red-100 text-red-800"
                : "bg-slate-200 text-slate-700"
            }`}
          >
            allow_real: {String(futu.allow_real)}
          </span>
        </div>
      </section>
    </div>
  );
}
