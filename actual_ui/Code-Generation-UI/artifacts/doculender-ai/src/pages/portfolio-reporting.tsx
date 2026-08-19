import { BarChart3, Database, ShieldAlert } from 'lucide-react';

import { RequestChatWidget } from '@/components/request-chat-widget';

export default function PortfolioReporting() {
  return (
    <div className="mx-auto max-w-6xl px-5 py-8 md:px-8 md:py-10">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div>
          <div className="mb-6 flex items-start gap-4">
            <div className="grid h-11 w-11 shrink-0 place-items-center rounded-md bg-[#e7eee8] text-[#39745e]">
              <BarChart3 size={22} />
            </div>
            <div>
              <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-[#39745e]">Operations workspace</div>
              <h1 className="mt-1 font-serif text-3xl tracking-tight text-foreground md:text-4xl">Portfolio reporting desk</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Ask the reporting agent for cross-portfolio operational insights without selecting an individual credit memo request.</p>
            </div>
          </div>
          <RequestChatWidget mode="portfolio" />
        </div>

        <aside className="space-y-4">
          <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
            <div className="flex items-center gap-2 text-sm font-semibold"><Database size={16} className="text-[#39745e]" /> Available reporting data</div>
            <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">Curated operational views</div>
            <ul className="mt-4 space-y-3 text-xs leading-5 text-foreground/75">
              <li>Loan stages and committee dates</li>
              <li>Relationship and operating-account status</li>
              <li>Risk grades, monitoring, and watchlists</li>
              <li>Credit-condition ownership and due dates</li>
              <li>Collateral-control status and targets</li>
            </ul>
          </section>
          <section className="rounded-lg border border-[#e7d3ad] bg-[#f9f1df] p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-[#88642e]"><ShieldAlert size={16} /> POC reporting access</div>
            <p className="mt-2 text-xs leading-5 text-[#88642e]/85">Results are generated from approved read-only reporting views. Application-level admin authorization is planned before production use.</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
