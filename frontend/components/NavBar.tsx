"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import type { SettingsResponse } from "@/lib/types";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/signals", label: "Signals" },
  { href: "/execution", label: "Execution" },
  { href: "/orders", label: "Orders" },
  { href: "/backtest", label: "Backtest" },
  { href: "/history", label: "History" },
  { href: "/memory", label: "Memory" },
  { href: "/settings", label: "Settings" },
];

const MODE_STYLES: Record<string, string> = {
  advisory: "bg-slate-200 text-slate-700",
  semi_auto: "bg-amber-100 text-amber-800 border border-amber-300",
  full_auto: "bg-red-100 text-red-800 border border-red-300",
};

function TickerSearch() {
  const router = useRouter();
  const [ticker, setTicker] = useState("");

  function go() {
    const sym = ticker.trim().toUpperCase();
    if (!sym) return;
    router.push(`/stock/${sym}`);
    setTicker("");
  }

  return (
    <div className="flex items-center gap-1.5">
      <input
        value={ticker}
        onChange={(e) => setTicker(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") go();
        }}
        placeholder="代码…"
        aria-label="Ticker search"
        className="input w-24 sm:w-32"
      />
      <button
        onClick={go}
        className="rounded-md border border-slate-300 px-2.5 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
      >
        查
      </button>
    </div>
  );
}

export default function NavBar() {
  const pathname = usePathname();
  const [mode, setMode] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGet<SettingsResponse>("settings")
      .then((s) => {
        if (!cancelled) setMode(s.mode);
      })
      .catch(() => {
        if (!cancelled) setMode(null);
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-6">
          <span className="text-sm font-semibold tracking-tight text-slate-900">stock-agent</span>
          <nav className="flex gap-1">
            {LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  pathname?.startsWith(l.href)
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <TickerSearch />
          <span
            className={`rounded-full px-2.5 py-1 text-xs font-semibold uppercase tracking-wide ${
              mode ? MODE_STYLES[mode] ?? "bg-slate-200 text-slate-700" : "bg-slate-100 text-slate-400"
            }`}
          >
            {mode ?? "loading…"}
          </span>
        </div>
      </div>
    </header>
  );
}
