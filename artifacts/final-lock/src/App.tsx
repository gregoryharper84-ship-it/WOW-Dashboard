import { Switch, Route, Router as WouterRouter, Link, useLocation } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import FinalLockDashboard from "@/pages/final-lock-dashboard";
import PromptPage from "@/pages/prompt";
import { Lock, Sparkles } from "lucide-react";

const queryClient = new QueryClient();

function IconRail() {
  const [loc] = useLocation();
  const isPrompt = loc.startsWith("/analyze");

  const navItems = [
    {
      href: "/",
      label: "Final Lock",
      icon: Lock,
      active: !isPrompt,
    },
    {
      href: "/analyze",
      label: "Prompt",
      icon: Sparkles,
      active: isPrompt,
    },
  ];

  return (
    <nav className="fixed left-0 top-0 h-full w-14 bg-card border-r border-border flex flex-col items-center py-4 gap-1 z-20">
      {/* Logo dot */}
      <div className="w-7 h-7 rounded-lg bg-primary/25 flex items-center justify-center mb-3">
        <span className="text-primary font-black text-xs leading-none">W</span>
      </div>

      {navItems.map(({ href, label, icon: Icon, active }) => (
        <Link
          key={href}
          href={href}
          title={label}
          className={`w-10 h-10 rounded-xl flex items-center justify-center transition-colors ${
            active
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
  return (
    <>
      <IconRail />
      <div className="pl-14">
        <Switch>
          <Route path="/" component={FinalLockDashboard} />
          <Route path="/analyze" component={PromptPage} />
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
