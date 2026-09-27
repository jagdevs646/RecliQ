import { BarChart3, BookMarked, ClipboardList, Files, History, Plus, ScrollText, Tags, type LucideIcon } from "lucide-react";
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
  { page: "status", label: "Status", icon: ClipboardList },
  { page: "history", label: "History", icon: History },
  { page: "saved", label: "Saved setups", icon: BookMarked },
  { page: "aliases", label: "Name aliases", icon: Tags },
  { page: "audit", label: "Audit log", icon: ScrollText }
];

export function Shell({ activePage, onNavigate, children }: Props) {
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
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.page}
                type="button"
                className={activePage === item.page ? "active" : ""}
                onClick={() => onNavigate(item.page)}
              >
                <Icon size={18} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <button type="button" className="sidebar-new" onClick={() => onNavigate("upload")}><Plus size={17} />New reconciliation</button>
      </aside>
      <main>
        {children}
      </main>
    </div>
  );
}
