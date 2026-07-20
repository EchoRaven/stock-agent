import type { Metadata } from "next";
import type { ReactNode } from "react";

import NavBar from "@/components/NavBar";

import "./globals.css";

export const metadata: Metadata = {
  title: "stock-agent",
  description: "Operator dashboard for the stock-agent trading system",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <NavBar />
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
