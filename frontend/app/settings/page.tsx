"use client";

import { type FormEvent, useEffect, useState } from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { Mode, RiskParamKey, SettingsResponse } from "@/lib/types";
import { ErrorBanner } from "@/components/ui";

const MODES: { value: Mode; label: string; desc: string }[] = [
  { value: "advisory", label: "Advisory", desc: "仅生成建议,不下单" },
  { value: "semi_auto", label: "Semi-auto", desc: "下单需人工批准" },
  { value: "full_auto", label: "Full-auto", desc: "闸门通过后自动提交 — 高风险" },
];

const RISK_FIELDS: { key: RiskParamKey; label: string; kind: "pct" | "int" | "cash" }[] = [
  { key: "single_position_cap_pct", label: "单仓位占比上限", kind: "pct" },
  { key: "total_position_cap_pct", label: "总仓位占比上限", kind: "pct" },
  { key: "max_new_positions_per_day", label: "每日新开仓上限", kind: "int" },
  { key: "daily_loss_halt_pct", label: "单日亏损熔断阈值", kind: "pct" },
  { key: "cooldown_days", label: "冷却天数", kind: "int" },
  { key: "initial_cash", label: "初始资金", kind: "cash" },
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [modeBusy, setModeBusy] = useState(false);
  const [modeError, setModeError] = useState<string | null>(null);
  const [pendingMode, setPendingMode] = useState<Mode | null>(null);
  const [confirmChecked, setConfirmChecked] = useState(false);

  const [riskDraft, setRiskDraft] = useState<Record<string, string>>({});
  const [riskBusy, setRiskBusy] = useState(false);
  const [riskError, setRiskError] = useState<string | null>(null);
  const [riskNotice, setRiskNotice] = useState<string | null>(null);

  function syncDraft(s: SettingsResponse) {
    const draft: Record<string, string> = {};
    for (const f of RISK_FIELDS) draft[f.key] = String(s[f.key]);
    setRiskDraft(draft);
  }

  async function load() {
    try {
      const res = await apiGet<SettingsResponse>("settings");
      setSettings(res);
      syncDraft(res);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "failed to load settings");
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function applyMode(mode: Mode, confirm = false) {
    setModeBusy(true);
    setModeError(null);
    try {
      const body: { mode: Mode; confirm_full_auto?: boolean } = { mode };
      if (confirm) body.confirm_full_auto = true;
      const res = await apiPost<SettingsResponse>("settings/mode", body);
      setSettings(res);
      syncDraft(res);
      setPendingMode(null);
      setConfirmChecked(false);
    } catch (err) {
      setModeError(
        err instanceof ApiError ? ApiError.detailToMessage(err.detail) : "切换模式失败"
      );
    } finally {
      setModeBusy(false);
    }
  }

  function onSelectMode(mode: Mode) {
    if (mode === settings?.mode) return;
    setModeError(null);
    if (mode === "full_auto") {
      setPendingMode(mode);
      setConfirmChecked(false);
      return;
    }
    applyMode(mode);
  }

  async function onSubmitRisk(e: FormEvent) {
    e.preventDefault();
    if (!settings) return;
    setRiskBusy(true);
    setRiskError(null);
    setRiskNotice(null);
    try {
      const changed: Record<string, number> = {};
      for (const f of RISK_FIELDS) {
        const raw = riskDraft[f.key];
        const num = Number(raw);
        if (raw === "" || Number.isNaN(num)) continue;
        if (num !== settings[f.key]) changed[f.key] = num;
      }
      if (Object.keys(changed).length === 0) {
        setRiskNotice("没有变更");
        return;
      }
      const res = await apiPost<SettingsResponse>("settings/risk", changed);
      setSettings(res);
      syncDraft(res);
      setRiskNotice("已保存");
    } catch (err) {
      setRiskError(err instanceof ApiError ? ApiError.detailToMessage(err.detail) : "保存失败");
    } finally {
      setRiskBusy(false);
    }
  }

  if (loadError && !settings) return <ErrorBanner message={loadError} />;
  if (!settings) return <p className="text-sm text-slate-500">Loading…</p>;

  return (
    <div className="space-y-8">
      <section>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">运行模式</h2>
        <div className="flex flex-wrap gap-3">
          {MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => onSelectMode(m.value)}
              disabled={modeBusy}
              className={`rounded-md border px-4 py-2 text-left text-sm transition-colors disabled:opacity-50 ${
                settings.mode === m.value
                  ? m.value === "full_auto"
                    ? "border-red-400 bg-red-50 text-red-800"
                    : m.value === "semi_auto"
                      ? "border-amber-400 bg-amber-50 text-amber-800"
                      : "border-slate-400 bg-slate-100 text-slate-900"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-400"
              }`}
            >
              <div className="font-medium">{m.label}</div>
              <div className="text-xs opacity-80">{m.desc}</div>
            </button>
          ))}
        </div>
        {modeError && <p className="mt-2 text-sm text-red-600">{modeError}</p>}

        {pendingMode === "full_auto" && (
          <div className="mt-4 max-w-md rounded-md border border-red-300 bg-red-50 p-4">
            <p className="text-sm font-semibold text-red-800">开启 full_auto 需要显式确认</p>
            <p className="mt-1 text-xs text-red-700">
              开启后,系统会在闸门检查通过后自动提交订单,无需人工批准。
            </p>
            <label className="mt-3 flex items-center gap-2 text-sm text-red-800">
              <input
                type="checkbox"
                checked={confirmChecked}
                onChange={(e) => setConfirmChecked(e.target.checked)}
              />
              我确认开启全自动
            </label>
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => applyMode("full_auto", true)}
                disabled={!confirmChecked || modeBusy}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                确认开启
              </button>
              <button
                onClick={() => {
                  setPendingMode(null);
                  setConfirmChecked(false);
                }}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
              >
                取消
              </button>
            </div>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-1 text-sm font-semibold text-slate-700">风控参数</h2>
        <p className="mb-3 text-xs text-slate-500">这些是安全限制 — 修改前请确认理解其影响。</p>
        <form
          onSubmit={onSubmitRisk}
          className="grid max-w-2xl grid-cols-1 gap-4 rounded-md border border-slate-200 bg-white p-4 sm:grid-cols-2"
        >
          {RISK_FIELDS.map((f) => (
            <label key={f.key} className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-slate-700">{f.label}</span>
              <input
                type="number"
                step={f.kind === "pct" ? "0.01" : "1"}
                className="input"
                value={riskDraft[f.key] ?? ""}
                onChange={(e) =>
                  setRiskDraft((d) => ({ ...d, [f.key]: e.target.value }))
                }
              />
            </label>
          ))}
          <div className="col-span-1 flex items-center gap-3 sm:col-span-2">
            <button
              type="submit"
              disabled={riskBusy}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {riskBusy ? "保存中…" : "保存变更"}
            </button>
            {riskNotice && <span className="text-sm text-emerald-700">{riskNotice}</span>}
          </div>
        </form>
        {riskError && <p className="mt-2 text-sm text-red-600">{riskError}</p>}
      </section>
    </div>
  );
}
