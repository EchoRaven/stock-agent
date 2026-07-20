"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type {
  FactorMineResponse,
  MemoryEntry,
  MemoryKind,
  MemorySeedResponse,
  MemoryStatus,
} from "@/lib/types";
import { ErrorBanner, Field } from "@/components/ui";

const KIND_OPTIONS: { value: MemoryKind; label: string }[] = [
  { value: "insight", label: "洞察 insight" },
  { value: "factor", label: "因子 factor" },
  { value: "trade_review", label: "复盘 trade_review" },
  { value: "market_note", label: "市场笔记 market_note" },
];

const STATUS_OPTIONS: { value: MemoryStatus; label: string }[] = [
  { value: "validated", label: "已验证 validated" },
  { value: "refuted", label: "已证伪 refuted" },
  { value: "data_blocked", label: "数据受限 data_blocked" },
  { value: "proposed", label: "待验证 proposed" },
  { value: "active", label: "生效中 active" },
];

const STATUS_STYLES: Record<MemoryStatus, string> = {
  validated: "border border-emerald-300 bg-emerald-50 text-emerald-800",
  refuted: "border border-slate-300 bg-slate-100 text-red-700",
  data_blocked: "border border-amber-300 bg-amber-50 text-amber-800",
  proposed: "border border-indigo-300 bg-indigo-50 text-indigo-800",
  active: "border border-slate-300 bg-slate-100 text-slate-700",
};

const MINE_VERDICT_STYLES: Record<string, string> = {
  validated: "text-emerald-700",
  no_improvement: "text-amber-700",
  refuted: "text-slate-500",
  error: "text-red-600",
};

function parseEvidence(raw: string | null): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    return null;
  } catch {
    return null;
  }
}

interface MemoryDraft {
  kind: MemoryKind;
  title: string;
  body: string;
  symbol: string;
  status: MemoryStatus;
}

const EMPTY_DRAFT: MemoryDraft = {
  kind: "insight",
  title: "",
  body: "",
  symbol: "",
  status: "active",
};

export default function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [seedBusy, setSeedBusy] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);
  const [seedNotice, setSeedNotice] = useState<string | null>(null);

  const [mineBusy, setMineBusy] = useState(false);
  const [mineError, setMineError] = useState<string | null>(null);
  const [mineResult, setMineResult] = useState<FactorMineResponse | null>(null);

  const [kindFilter, setKindFilter] = useState<MemoryKind | "">("");
  const [statusFilter, setStatusFilter] = useState<MemoryStatus | "">("");
  const [search, setSearch] = useState("");

  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState<MemoryDraft>(EMPTY_DRAFT);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const res = await apiGet<MemoryEntry[]>("memory");
      setEntries(res);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "知识库加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSeed() {
    setSeedBusy(true);
    setSeedError(null);
    setSeedNotice(null);
    try {
      const res = await apiPost<MemorySeedResponse>("memory/seed", {});
      setSeedNotice(`已播种 ${res.inserted} 条`);
      await load();
    } catch (err) {
      setSeedError(
        err instanceof ApiError
          ? err.status === 403
            ? "缺少令牌 — 检查后端 .api_token"
            : ApiError.detailToMessage(err.detail)
          : "播种失败"
      );
    } finally {
      setSeedBusy(false);
    }
  }

  async function onMine() {
    if (
      !window.confirm(
        "让 AI 提出候选因子并自动两窗口回测?只有稳健改善的才会标为 validated(多数会被证伪)。较慢。"
      )
    )
      return;
    setMineBusy(true);
    setMineError(null);
    setMineResult(null);
    try {
      const res = await apiPost<FactorMineResponse>("factors/mine", { n: 3 });
      setMineResult(res);
      await load();
    } catch (err) {
      setMineError(
        err instanceof ApiError
          ? err.status === 403
            ? "缺少令牌 — 检查后端 .api_token"
            : ApiError.detailToMessage(err.detail)
          : "因子挖掘失败"
      );
    } finally {
      setMineBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const title = draft.title.trim();
    const body = draft.body.trim();
    if (!title || !body) {
      setFormError("标题和正文不能为空");
      return;
    }
    setFormBusy(true);
    setFormError(null);
    try {
      const payload: Record<string, unknown> = { kind: draft.kind, title, body, status: draft.status };
      const symbol = draft.symbol.trim();
      if (symbol) payload.symbol = symbol.toUpperCase();
      const created = await apiPost<MemoryEntry>("memory", payload);
      setEntries((prev) => (prev ? [created, ...prev] : [created]));
      setDraft(EMPTY_DRAFT);
      setShowForm(false);
    } catch (err) {
      let message = "添加失败";
      if (err instanceof ApiError) {
        if (err.status === 403) message = "缺少令牌 — 检查后端 .api_token";
        else if (err.status === 400) message = `kind 无效: ${ApiError.detailToMessage(err.detail)}`;
        else if (err.status === 422) message = `校验失败: ${ApiError.detailToMessage(err.detail)}`;
        else message = ApiError.detailToMessage(err.detail);
      }
      setFormError(message);
    } finally {
      setFormBusy(false);
    }
  }

  const filtered = useMemo(() => {
    if (!entries) return [];
    const q = search.trim().toLowerCase();
    return entries.filter((e) => {
      if (kindFilter && e.kind !== kindFilter) return false;
      if (statusFilter && e.status !== statusFilter) return false;
      if (q && !e.title.toLowerCase().includes(q) && !e.body.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [entries, kindFilter, statusFilter, search]);

  if (loadError && !entries) return <ErrorBanner message={loadError} />;
  if (!entries) return <p className="text-sm text-slate-500">Loading…</p>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">知识库 / Memory</h1>
        <p className="text-sm text-slate-500">
          agent 积累的洞察、因子结论、复盘与市场笔记 — 仅作为委员会提示词的说明性上下文,不参与风控/下单判定。
        </p>
      </div>

      {loadError && <ErrorBanner message={loadError} />}

      {entries.length === 0 && (
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-sm text-slate-600">知识库为空。</p>
          <button
            onClick={onSeed}
            disabled={seedBusy}
            className="mt-3 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {seedBusy ? "播种中…" : "播种实验结论"}
          </button>
          {seedError && <p className="mt-2 text-sm text-red-600">{seedError}</p>}
          {seedNotice && <p className="mt-2 text-sm text-emerald-700">{seedNotice}</p>}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value as MemoryKind | "")}
          className="input"
        >
          <option value="">全部类型</option>
          {KIND_OPTIONS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as MemoryStatus | "")}
          className="input"
        >
          <option value="">全部状态</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="搜索标题/正文…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input min-w-[200px] flex-1"
        />
        <button
          onClick={() => load()}
          disabled={loading}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          {showForm ? "收起" : "+ 添加知识"}
        </button>
        <button
          onClick={onMine}
          disabled={mineBusy}
          className="rounded-md border border-indigo-300 bg-indigo-50 px-4 py-2 text-sm font-medium text-indigo-800 hover:bg-indigo-100 disabled:opacity-50"
        >
          {mineBusy ? "挖掘中…" : "挖掘一轮因子"}
        </button>
      </div>

      {mineBusy && (
        <p className="text-sm text-slate-500">
          因子挖掘中…提案 + 两窗口回测,可能要一两分钟。
        </p>
      )}
      {mineError && <ErrorBanner message={mineError} />}
      {mineResult && (
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="mb-2 text-sm font-medium text-slate-700">
            本轮挖掘完成,共 {mineResult.count} 条提案(结果已并入下方知识库):
          </p>
          <ul className="space-y-1 text-sm">
            {mineResult.results.map((r, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono text-slate-800">
                  {r.factor}({JSON.stringify(r.params)})
                </span>
                <span className="text-slate-400">→</span>
                <span className={`font-semibold ${MINE_VERDICT_STYLES[r.verdict] ?? "text-slate-600"}`}>
                  {r.verdict}
                </span>
                {r.verdict === "error" && r.error && (
                  <span className="text-xs text-red-500">{r.error}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {showForm && (
        <form
          onSubmit={onSubmit}
          className="grid grid-cols-1 gap-4 rounded-md border border-slate-200 bg-white p-4 sm:grid-cols-2"
        >
          <Field label="类型 kind">
            <select
              value={draft.kind}
              onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value as MemoryKind }))}
              className="input"
            >
              {KIND_OPTIONS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="状态 status">
            <select
              value={draft.status}
              onChange={(e) => setDraft((d) => ({ ...d, status: e.target.value as MemoryStatus }))}
              className="input"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="标题 title">
            <input
              type="text"
              value={draft.title}
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
              className="input"
              required
            />
          </Field>
          <Field label="标的 symbol(可选)">
            <input
              type="text"
              placeholder="AAPL"
              value={draft.symbol}
              onChange={(e) => setDraft((d) => ({ ...d, symbol: e.target.value }))}
              className="input"
            />
          </Field>
          <div className="col-span-1 sm:col-span-2">
            <Field label="正文 body">
              <textarea
                value={draft.body}
                onChange={(e) => setDraft((d) => ({ ...d, body: e.target.value }))}
                className="input min-h-[100px]"
                required
              />
            </Field>
          </div>
          <div className="col-span-1 flex items-center gap-3 sm:col-span-2">
            <button
              type="submit"
              disabled={formBusy}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {formBusy ? "提交中…" : "保存"}
            </button>
            {formError && <span className="text-sm text-red-600">{formError}</span>}
          </div>
        </form>
      )}

      {entries.length > 0 && filtered.length === 0 && (
        <p className="text-sm text-slate-500">没有匹配的条目</p>
      )}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {filtered.map((entry) => {
          const evidence = parseEvidence(entry.evidence_json);
          const evidenceEntries = evidence ? Object.entries(evidence) : [];
          return (
            <div
              key={entry.id}
              className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    STATUS_STYLES[entry.status] ?? "border border-slate-300 bg-slate-100 text-slate-700"
                  }`}
                >
                  {entry.status}
                </span>
                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                  {entry.kind}
                </span>
                {entry.symbol && (
                  <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                    {entry.symbol}
                  </span>
                )}
              </div>
              <div className="font-semibold text-slate-900">{entry.title}</div>
              <p className="whitespace-pre-wrap text-sm text-slate-700">{entry.body}</p>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-400">
                {entry.source && <span>来源: {entry.source}</span>}
                {evidenceEntries.map(([k, v]) => (
                  <span key={k}>
                    {k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </span>
                ))}
                <span>{entry.updated_at.slice(0, 10)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
