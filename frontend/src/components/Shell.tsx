import { BarChart3, BookMarked, Brain, Files, History, ScrollText, Tags, Wand2, type LucideIcon } from "lucide-react";
import { useEffect, useRef } from "react";
import type React from "react";
import type { Page } from "../types";

interface Props {
  activePage: Page;
  onNavigate: (page: Page) => void;
  children: React.ReactNode;
}

const navItems: Array<{ page: Page; label: string; icon: LucideIcon }> = [
  { page: "dashboard", label: "Dashboard", icon: BarChart3 },
  { page: "upload", label: "Reconcile", icon: Files },
  // No "Status" entry: a run's live status opens by itself when the run starts,
  // and from any queued or running job on the Dashboard or History.
  { page: "history", label: "History", icon: History },
  { page: "saved", label: "Saved setups", icon: BookMarked },
  { page: "aliases", label: "Name aliases", icon: Tags },
  { page: "rules", label: "Auto-resolution", icon: Wand2 },
  { page: "learning", label: "Learning", icon: Brain },
  { page: "audit", label: "Audit log", icon: ScrollText }
];

export function Shell({ activePage, onNavigate, children }: Props) {
  const navRef = useRef<HTMLElement>(null);
  // A run's live status and results belong to History, so the menu still shows where you are.
  const section: Page = activePage === "results" || activePage === "status" ? "history" : activePage;

  // On narrow screens the menu is a scrolling strip; keep the current page in view.
  useEffect(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>("button.active");
    if (nav && active && nav.scrollWidth > nav.clientWidth) {
      nav.scrollLeft = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2;
    }
  }, [section]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img src="/icon.ico" alt="RecliQ Logo" className="brand-logo" />
          <div>
            <strong>RecliQ</strong>
            <small>One click reconciliation</small>
          </div>
        </div>
        <nav ref={navRef} aria-label="Main">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = section === item.page;
            return (
              <button
                key={item.page}
                type="button"
                className={active ? "active" : ""}
                aria-current={active ? "page" : undefined}
                onClick={() => onNavigate(item.page)}
              >
                <Icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>
      <main>
        {children}
      </main>
    </div>
  );
}
