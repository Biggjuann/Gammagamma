import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Gammagamma — Real-Time Gamma Exposure Intelligence",
  description:
    "Real-time GEX, structural levels, regime detection, and original signals for options traders. OPRA data via Databento."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-ink-900 text-slate-100 min-h-screen bg-radial-fade">
        <header className="border-b border-white/5 backdrop-blur-md sticky top-0 z-40">
          <div className="mx-auto max-w-[1600px] flex items-center gap-6 px-6 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <span className="text-sonar-cyan text-xl">◎</span>
              <span>Gamma<span className="text-sonar-cyan">gamma</span></span>
            </Link>
            <nav className="flex gap-4 text-sm text-slate-300">
              <Link href="/" className="hover:text-white">Dashboard</Link>
              <Link href="/range" className="hover:text-white">Range</Link>
              <Link href="/universe" className="hover:text-white">Universe</Link>
              <Link href="/methodology" className="hover:text-white">Methodology</Link>
            </nav>
            <div className="ml-auto flex items-center gap-3 text-xs text-slate-400">
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-sonar-green animate-pulse" />
                OPRA via Databento
              </span>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-[1600px] px-6 py-6">{children}</main>
        <footer className="mt-16 border-t border-white/5">
          <div className="mx-auto max-w-[1600px] px-6 py-6 text-xs text-slate-500">
            Gammagamma provides structural market analytics for educational purposes only.
            Nothing on this platform constitutes investment advice, a trading signal, or a
            recommendation.
          </div>
        </footer>
      </body>
    </html>
  );
}
