import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import { Route, Switch, Router as WouterRouter } from 'wouter';
import { WorkspaceShell } from '@/components/workspace-shell';
import Home from '@/pages/home';
import Memo from '@/pages/memo';
import { RequestIdProvider } from '@/lib/request-id-context';

const queryClient = new QueryClient();

function Router() {
  return (
    <WorkspaceShell>
      <Switch>
        <Route path="/" component={Home} />
        <Route path="/memo" component={Memo} />
        <Route component={NotFound} />
      </Switch>
    </WorkspaceShell>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RequestIdProvider>
          <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
            <Router />
          </WouterRouter>
          <Toaster />
        </RequestIdProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
