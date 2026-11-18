import type { Metadata } from "next";
import Link from "next/link";
import { PrimaryNavigation } from "@/components/navigation";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mimic — Synthetic Customer Research",
  description:
    "Put your product in front of AI customers from your target audience and get evidence-based feedback.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="app-frame">
          <aside className="sidebar">
            <Link href="/studies" className="brand" aria-label="Mimic home">
              <span className="brand-mark" aria-hidden="true">M</span>
              <span>Mimic</span>
            </Link>
            <p className="workspace-label">Customer research</p>
            <PrimaryNavigation />
            <div className="sidebar-footer">
              <span className="online-dot" aria-hidden="true" />
              Research workspace ready
            </div>
          </aside>
          <div className="app-content">
            <header className="mobile-header">
              <Link href="/studies" className="brand"><span className="brand-mark">M</span> Mimic</Link>
              <PrimaryNavigation mobile />
            </header>
            <main className="shell">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
