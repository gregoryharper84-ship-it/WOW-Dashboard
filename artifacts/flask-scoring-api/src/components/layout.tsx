import { useState } from "react";
import { Link, useLocation } from "wouter";
import {
  Sparkles, LayoutGrid, BarChart2, Clock,
  Key, ChevronRight, Eye, EyeOff, Activity,
} from "lucide-react";
import { useApiKey } from "@/hooks/use-api-key";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/",       label: "Analyze",  icon: Sparkles   },
  { href: "/lobby",  label: "Lobby",    icon: LayoutGrid  },
  { href: "/stats",  label: "Stats",    icon: BarChart2   },
  { href: "/log",    label: "Log",      icon: Clock       },
];

interface LayoutProps {
  children: React.ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const [location] = useLocation();
  const { apiKey, setApiKey } = useApiKey();
  const [expanded, setExpanded] = useState(false);
  const [showKey, setShowKey] = useState(false);

  return (
    <div className="flex min-h-screen bg-background">
      {/* Sidebar */}
      <aside
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
        className={cn(
          "flex flex-col border-r border-border bg-sidebar transition-all duration-200 shrink-0 z-20",
          expanded ? "w-52" : "w-14"
        )}
        data-testid="sidebar"
      >
        {/* Wordmark */}
        <div className="flex items-center h-14 px-3.5 border-b border-border gap-2.5 overflow-hidden">
          <div className="w-7 h-7 rounded-lg bg-primary/15 border border-primary/25 flex items-center justify-center shrink-0">
            <Activity size={14} className="text-primary" />
          </div>
          <div
            className={cn(
              "transition-all duration-200 overflow-hidden whitespace-nowrap",
              expanded ? "opacity-100 max-w-[120px]" : "opacity-0 max-w-0"
            )}
          >
            <span className="text-sm font-black tracking-tight text-foreground">WOW</span>
            <span className="ml-1 text-[10px] font-bold text-primary uppercase tracking-widest">v16</span>
          </div>
          <button
            data-testid="button-toggle-sidebar"
            onClick={() => setExpanded((v) => !v)}
            className={cn(
              "ml-auto p-1 rounded text-muted-foreground hover:text-foreground transition-all shrink-0",
              expanded ? "opacity-100" : "opacity-0 pointer-events-none"
            )}
          >
            <ChevronRight size={13} className={cn("transition-transform", !expanded && "rotate-180")} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 px-2 space-y-0.5">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? location === "/" : location.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                data-testid={`link-nav-${label.toLowerCase().replace(/\s+/g, "-")}`}
                className={cn(
                  "flex items-center gap-2.5 px-2.5 py-2.5 rounded-lg text-sm font-semibold transition-colors overflow-hidden",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent"
                )}
              >
                <Icon size={17} className="shrink-0" />
                <span
                  className={cn(
                    "transition-all duration-200 whitespace-nowrap overflow-hidden",
                    expanded ? "opacity-100 max-w-[120px]" : "opacity-0 max-w-0"
                  )}
                >
                  {label}
                </span>
              </Link>
            );
          })}
        </nav>

        {/* API Key slot */}
        <div className="px-2 pb-4 border-t border-border pt-3">
          {expanded ? (
            <div>
              <div className="flex items-center gap-1.5 mb-1.5 px-1">
                <Key size={11} className="text-muted-foreground shrink-0" />
                <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                  API Key
                </span>
              </div>
              <div className="relative">
                <input
                  data-testid="input-api-key"
                  type={showKey ? "text" : "password"}
                  placeholder="Paste key here…"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  className="w-full text-[11px] bg-background border border-border rounded-md px-2.5 py-1.5 pr-7 text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
                />
                <button
                  data-testid="button-toggle-key-visibility"
                  type="button"
                  onClick={() => setShowKey((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showKey ? <EyeOff size={11} /> : <Eye size={11} />}
                </button>
              </div>
              {apiKey && (
                <p className="mt-1 px-1 text-[10px] text-primary font-medium">Key saved</p>
              )}
            </div>
          ) : (
            <div className="flex justify-center">
              <div
                className={cn(
                  "w-2 h-2 rounded-full",
                  apiKey ? "bg-emerald-400" : "bg-muted-foreground/30"
                )}
                title={apiKey ? "API key set" : "No API key"}
              />
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 overflow-auto">
        {children}
      </main>
    </div>
  );
}
