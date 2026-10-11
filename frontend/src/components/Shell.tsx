import { Activity, BarChart3, BookMarked, Brain, Eye, Files, Gauge, History, Lightbulb, LifeBuoy, LogIn, LogOut, Mail, MessageSquareText, Settings, Shield, SlidersHorizontal, Tags, Users, Wand2, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type React from "react";
import { api } from "../services/api";
import type { AccountIdentity, AdminSection, Page } from "../types";

interface Props {
  activePage: Page;
  activeAdminSection: AdminSection;
  onNavigate: (page: Page) => void;
  onNavigateAdmin: (section: AdminSection) => void;
  identity: AccountIdentity;
  onSignIn: () => void;
  onSignOut: () => void;
  children: React.ReactNode;
}

type NavItem = { page?: Page; section?: AdminSection; label: string; icon: LucideIcon };

const userNav: NavItem[] = [
  { page: "dashboard", label: "Dashboard", icon: BarChart3 },
  { page: "upload", label: "Reconcile", icon: Files },
  { page: "history", label: "History", icon: History },
  { page: "saved", label: "Saved setups", icon: BookMarked },
  { page: "aliases", label: "Name aliases", icon: Tags },
  { page: "rules", label: "Auto-resolution", icon: Wand2 },
  { page: "learning", label: "Learning", icon: Brain },
  { page: "audit", label: "Audit log", icon: Activity },
  { page: "support", label: "Support", icon: LifeBuoy },
  { page: "settings", label: "Settings", icon: Settings },
];

const adminNav: NavItem[] = [
  { section: "dashboard", label: "Dashboard", icon: BarChart3 },
  { section: "users", label: "User management", icon: Users },
  { section: "features", label: "Feature management", icon: SlidersHorizontal },
  { section: "limits", label: "Usage & limits", icon: Gauge },
  { section: "insights", label: "Product insights", icon: Lightbulb },
  { section: "complaints", label: "Complaints", icon: MessageSquareText },
  { section: "activity", label: "Audit & activity", icon: Shield },
  { section: "email", label: "Email management", icon: Mail },
  { section: "preview", label: "User experience preview", icon: Eye },
  { page: "settings", label: "Admin settings", icon: Settings },
];

const featureForPage: Partial<Record<Page, string>> = {
  upload: "basic_reconciliation",
  saved: "saved_setups",
  aliases: "near_match_review",
  learning: "near_match_review",
  rules: "auto_resolution",
};

export function Shell({ activePage, activeAdminSection, onNavigate, onNavigateAdmin, identity, onSignIn, onSignOut, children }: Props) {
  const navRef = useRef<HTMLElement>(null);
  const [unreadComplaints, setUnreadComplaints] = useState(0);
  const isAdmin = identity.user?.role === "super_admin" && identity.user.mfa_enabled;
  const section: Page = activePage === "results" || activePage === "status" ? "history" : activePage;

  useEffect(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>("button.active");
    if (nav && active && nav.scrollWidth > nav.clientWidth) active.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [section, activeAdminSection]);

  useEffect(() => {
    if (!isAdmin) { setUnreadComplaints(0); return; }
    let current = true;
    const refresh = () => { void api.adminComplaintSummary().then((result) => { if (current) setUnreadComplaints(result.unread); }).catch(() => undefined); };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => { current = false; window.clearInterval(timer); };
  }, [isAdmin]);

  const items = isAdmin ? adminNav : userNav.filter((item) => {
    const page = item.page;
    if (!page) return true;
    const feature = featureForPage[page];
    if (page === "upload") return identity.features?.basic_reconciliation !== false || identity.features?.gst_reconciliation !== false;
    return !feature || identity.features?.[feature] !== false;
  });

  return (
    <div className={`app-shell${isAdmin ? " is-admin-shell" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <img src="/icon.ico" alt="RecliQ Logo" className="brand-logo" />
          <div><strong>RecliQ</strong><small>{isAdmin ? "Platform administration" : "One click reconciliation"}</small></div>
        </div>
        <nav ref={navRef} aria-label={isAdmin ? "Administration" : "Main"}>
          {items.map((item) => {
            const Icon = item.icon;
            const active = item.section ? activePage === "admin" && activeAdminSection === item.section : section === item.page;
            return <button key={item.section ?? item.page} type="button" className={active ? "active" : ""} aria-current={active ? "page" : undefined} aria-label={item.section === "complaints" && unreadComplaints ? `${item.label}, ${unreadComplaints} unread` : undefined} onClick={() => item.section ? onNavigateAdmin(item.section) : item.page && onNavigate(item.page)}>
              <Icon size={18} aria-hidden="true" />
              <span>{item.label}</span>
              {item.section === "complaints" && unreadComplaints > 0 && <span className="nav-count" aria-hidden="true">{unreadComplaints > 99 ? "99+" : unreadComplaints}</span>}
            </button>;
          })}
        </nav>
        <div className="sidebar-account">
          <div className="account-summary"><span className="account-avatar">{identity.user?.display_name?.slice(0, 1).toUpperCase() || "G"}</span><div><strong>{identity.user?.display_name || "Guest workspace"}</strong><small>{identity.user?.email || "This browser, kept for one day"}</small></div></div>
          {identity.authenticated ? <button type="button" className="sidebar-account-action" onClick={onSignOut}><LogOut size={16} /><span>Sign out</span></button> : <button type="button" className="sidebar-account-action" onClick={onSignIn}><LogIn size={16} /><span>{identity.capabilities?.registration === false ? "Sign in" : "Sign in or create account"}</span></button>}
        </div>
      </aside>
      <main>{children}</main>
    </div>
  );
}
