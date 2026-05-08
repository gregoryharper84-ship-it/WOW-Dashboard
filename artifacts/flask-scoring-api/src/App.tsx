import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Layout from "@/components/layout";
import Lobby from "@/pages/lobby";
import Score from "@/pages/score";
import Stats from "@/pages/stats";
import Log from "@/pages/log";

const queryClient = new QueryClient();

function Router() {
  return (
    <Switch>
      <Route path="/" component={Lobby} />
      <Route path="/score" component={Score} />
      <Route path="/stats" component={Stats} />
      <Route path="/log" component={Log} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Layout>
            <Router />
          </Layout>
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
