import { Switch, Route, Router as WouterRouter, Link, useLocation } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import FinalLockDashboard from "@/pages/final-lock-dashboard";
import V17RequestsPage from "@/pages/v17-requests";
import PromptPage from "@/pages/prompt";
import KalshiPage from "@/pages/kalshi";
import RequestLogPage from "@/pages/request-log";
import LeaderboardPage from "@/pages/leaderboard";
import PropsIntakePage from "@/pages/props-intake";
import RankingsPage from "@/pages/rankings";
import HistoryPage from "@/pages/history";
import SourceHealthPage from "@/pages/source-health";
import BacktestingPage from "@/pages/backtesting";
import {
  Lock, Sparkles, TrendingUp, ScrollText, Trophy, Database,
  BarChart2, Clock, Activity, FlaskConical,
} from "lucide-react";

const queryClient = new QueryClient();

function IconRail() {
  const [loc] = useLocation();

  const navItems = [
    { href: "/",           label: "V17 Requests",  icon: Lock,          match: (l: string) => l === "/" },
    { href: "/legacy",     label: "Legacy V16 Form", icon: FlaskConical, match: (l: string) => l.startsWith("/legacy") },
    { href: "/rankings",   label: "Rankings",      icon: BarChart2,     match: (l: string) => l.startsWith("/rankings") },
    { href: "/history",    label: "Predictions",   icon: Clock,         match: (l: string) => l.startsWith("/history") },
    { href: "/backtest",   label: "Backtesting",   icon: FlaskConical,  match: (l: string) => l.startsWith("/backtest") },
    { href: "/health",     label: "Source Health", icon: Activity,      match: (l: string) => l.startsWith("/health") },
    { href: "/props",      label: "Props Intake",  icon: Database,      match: (l: string) => l.startsWith("/props") },
    { href: "/analyze",    label: "Prompt",        icon: Sparkles,      match: (l: string) => l.startsWith("/analyze") },
    { href: "/kalshi",     label: "Kalshi",        icon: TrendingUp,    match: (l: string) => l.startsWith("/kalshi") },
    { href: "/logs",       label: "Request Log",   icon: ScrollText,    match: (l: string) => l.startsWith("/logs") },
    { href: "/leaderboard",label: "Leaderboard",   icon: Trophy,        match: (l: string) => l.startsWith("/leaderboard") },
  ];

  return (
    <nav className="fixed left-0 top-0 h-full w-14 bg-card border-r border-border flex flex-col items-center py-4 gap-1 z-20">
      {/* Logo dot */}
      <div className="w-7 h-7 rounded-lg bg-primary/25 flex items-center justify-center mb-3">
        <span className="text-primary font-black text-xs leading-none">W</span>
      </div>

      {navItems.map(({ href, label, icon: Icon, match }) => (
        <Link
          key={href}
          href={href}
          title={label}
          aria-label={label}
          className={`w-10 h-10 rounded-xl flex items-center justify-center transition-colors ${
            match(loc)
              ? "bg-primary/20 text-primary"
              : "text-muted-foreground hover:bg-muted hover:text-foreground"
          }`}
        >
          <Icon size={18} />
        </Link>
      ))}
    </nav>
  );
}

function Router() {
  const [loc] = useLocation();
  return (
    <>
      <IconRail />
      <div className="pl-14">
        {loc !== "/" && <div className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">Legacy V16 compatibility view. These records and estimates are separate from governed V17 receipts. <Link href="/" className="underline">Open V17 Requests and configure dashboard access</Link>.</div>}
        <Switch>
          <Route path="/"            component={V17RequestsPage} />
          <Route path="/legacy"      component={FinalLockDashboard} />
          <Route path="/rankings"    component={RankingsPage} />
          <Route path="/history"     component={HistoryPage} />
          <Route path="/backtest"    component={BacktestingPage} />
          <Route path="/health"      component={SourceHealthPage} />
          <Route path="/props"       component={PropsIntakePage} />
          <Route path="/analyze"     component={PromptPage} />
          <Route path="/kalshi"      component={KalshiPage} />
          <Route path="/logs"        component={RequestLogPage} />
          <Route path="/leaderboard" component={LeaderboardPage} />
          <Route component={NotFound} />
        </Switch>
      </div>
    </>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
