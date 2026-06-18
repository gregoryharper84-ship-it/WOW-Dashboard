import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Layout from "@/components/layout";
import Prompt from "@/pages/prompt";
import Lobby from "@/pages/lobby";
import Stats from "@/pages/stats";
import Log from "@/pages/log";

const queryClient = new QueryClient();

function Router() {
  return (
    <Switch>
      <Route path="/" component={Prompt} />
      <Route path="/lobby" component={Lobby} />
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
