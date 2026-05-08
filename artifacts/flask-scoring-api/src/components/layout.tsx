import { useState } from "react";
import { Link, useLocation } from "wouter";
import { LayoutGrid, Zap, BarChart2, Clock, Key, ChevronLeft, ChevronRight, Eye, EyeOff } from "lucide-react";
import { useApiKey } from "@/hooks/use-api-key";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", label: "Lobby", icon: LayoutGrid },
  { href: "/score", label: "Score a Pick", icon: Zap },
  { href: "/stats", label: "Stats", icon: BarChart2 },
  { href: "/log", label: "Log", icon: Clock },
];

interface LayoutProps {
  children: React.ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const [location] = useLocation();
  const { apiKey, setApiKey } = useApiKey();
  const [collapsed, setCollapsed] = useState(false);
  const [showKey, setShowKey] = useState(false);

  return (
    <div className="flex min-h-screen bg-background">
      <aside
        className={cn(
          "flex flex-col border-r border-border bg-sidebar transition-all duration-300 shrink-0",
          collapsed ? "w-16" : "w-60"
        )}
        data-testid="sidebar"
      >
        <div className="flex items-center justify-between px-4 py-5 border-b border-border">
          {!collapsed && (
            <div>
              <span className="text-xl font-black tracking-tight text-foreground">WOW</span>
              <span className="ml-1 text-xs font-semibold text-primary uppercase tracking-widest">Scoring</span>
            </div>
          )}
          <button
            data-testid="button-toggle-sidebar"
            onClick={() => setCollapsed((c) => !c)}
            className="ml-auto p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>

        <nav className="flex-1 py-4 px-2 space-y-1">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? location === "/" : location.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                data-testid={`link-nav-${label.toLowerCase().replace(/\s+/g, "-")}`}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent"
                )}
              >
                <Icon size={18} className="shrink-0" />
                {!collapsed && <span>{label}</span>}
              </Link>
            );
          })}
        </nav>

        {!collapsed && (
          <div className="px-3 pb-5 border-t border-border pt-4">
            <div className="flex items-center gap-2 mb-1.5">
              <Key size={13} className="text-muted-foreground shrink-0" />
              <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">API Key</span>
            </div>
            <div className="relative">
              <input
                data-testid="input-api-key"
                type={showKey ? "text" : "password"}
                placeholder="Paste key here…"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full text-xs bg-background border border-border rounded-md px-2.5 py-2 pr-8 text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
              <button
                data-testid="button-toggle-key-visibility"
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showKey ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>
            {apiKey && (
              <p className="mt-1 text-xs text-primary">Key saved</p>
            )}
          </div>
        )}
      </aside>

      <main className="flex-1 min-w-0 overflow-auto">
        {children}
      </main>
    </div>
  );
}
