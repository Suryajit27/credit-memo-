import { Bell, ChevronDown, CloudUpload, FileStack, LayoutDashboard, PanelLeftClose, PanelLeftOpen, PenLine, Search, Settings2, ShieldCheck } from 'lucide-react';
import { Link, useLocation } from 'wouter';
import { useEffect, useMemo, useState } from 'react';
import { useGetIndexerStatus, getGetIndexerStatusQueryKey } from '@workspace/api-client-react';

import { useRequestId } from '@/lib/request-id-context';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Dialog, DialogContent } from '@/components/ui/dialog';

type WorkspaceShellProps = { children: React.ReactNode };

export function WorkspaceShell({ children }: WorkspaceShellProps) {
  const [location, navigate] = useLocation();
  const [railOpen, setRailOpen] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const { requestId } = useRequestId();
  const indexer = useGetIndexerStatus({ requestId }, { query: { queryKey: getGetIndexerStatusQueryKey({ requestId }), enabled: Boolean(requestId), refetchInterval: (q) => { const s = q.state.data?.indexingStatus?.toLowerCase(); return s === 'succeeded' || s === 'failed' ? false : 5000; } } });
  const indexed = indexer.data;
  const navItems = useMemo(() => [
    { href: '/', label: 'Document intake', detail: 'Classify & index', icon: FileStack, keywords: ['intake', 'document intake', 'documents', 'classify', 'index'] },
    { href: '/memo', label: 'Narrative studio', detail: 'Draft memo narratives', icon: PenLine, keywords: ['memo', 'credit memo', 'narrative', 'narratives', 'studio', 'draft', 'review'] },
  ], []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const isShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k';
      if (!isShortcut) return;
      event.preventDefault();
      setSearchOpen(true);
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <div className="min-h-[100dvh] bg-background text-foreground">
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-[248px] flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-transform duration-300 ${railOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex h-[74px] items-center gap-3 border-b border-sidebar-border px-5">
          <div className="grid h-9 w-9 place-items-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground">
            <ShieldCheck size={19} strokeWidth={2.5} />
          </div>
          <div>
            <div className="font-serif text-[23px] leading-none tracking-tight">DocuLender</div>
            <div className="mt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-sidebar-foreground/55">AI underwriting desk</div>
          </div>
        </div>
        <div className="px-3 pt-6">
          <div className="mb-2 px-3 font-mono text-[9px] uppercase tracking-[0.18em] text-sidebar-foreground/40">Workspace</div>
          <nav className="space-y-1">
            {navItems.map(({ href, label, detail, icon: Icon }) => {
              const active = location === href;
              return (
                <Link key={href} href={href} data-testid={`link-${label.toLowerCase().replaceAll(' ', '-')}`} className={`group flex items-center gap-3 rounded-md px-3 py-3 transition-colors ${active ? 'bg-sidebar-accent text-sidebar-accent-foreground' : 'text-sidebar-foreground/65 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground'}`}>
                  <Icon size={17} strokeWidth={active ? 2.4 : 1.8} />
                  <span className="min-w-0">
                    <span className="block text-[13px] font-semibold">{label}</span>
                    <span className="mt-0.5 block truncate font-mono text-[9px] uppercase tracking-[0.08em] opacity-45">{detail}</span>
                  </span>
                  {active && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-sidebar-primary" />}
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="mt-auto px-3 pb-5">
          {requestId ? (
            <div className="mb-3 rounded-md border border-sidebar-border bg-sidebar-accent/40 p-3">
              <div className="flex items-center gap-2 text-[11px] font-semibold"><CloudUpload size={13} className="text-sidebar-primary" /> Azure index</div>
              <div className="mt-2 flex items-center justify-between font-mono text-[9px] uppercase tracking-[0.12em] text-sidebar-foreground/45"><span>{indexed?.indexingStatus ?? 'pending'}</span><span className="text-sidebar-primary">{indexed?.totalCount ? Math.round((indexed.indexedCount / indexed.totalCount) * 100) : 0}%</span></div>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-sidebar-border"><div className="h-full rounded-full bg-sidebar-primary transition-all duration-700" style={{ width: `${indexed?.totalCount ? Math.round((indexed.indexedCount / indexed.totalCount) * 100) : 0}%` }} /></div>
              {indexed?.indexingErrors?.length ? <div className="mt-1.5 font-mono text-[8px] uppercase tracking-[0.1em] text-sidebar-foreground/40">{indexed.indexingErrors.join('; ')}</div> : null}
            </div>
          ) : null}
          <button type="button" data-testid="button-settings" className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-xs text-sidebar-foreground/55 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"><Settings2 size={16} /> Workspace settings</button>
          <div className="mt-4 flex items-center gap-3 border-t border-sidebar-border pt-4">
            <div className="grid h-8 w-8 place-items-center rounded-full bg-[#d6c28d] text-[11px] font-bold text-sidebar">MC</div>
            <div className="min-w-0 flex-1"><div className="truncate text-xs font-semibold">Morgan Chen</div><div className="truncate font-mono text-[9px] uppercase tracking-[0.1em] text-sidebar-foreground/40">Credit operations</div></div>
            <ChevronDown size={14} className="text-sidebar-foreground/35" />
          </div>
        </div>
      </aside>
      {railOpen && <button aria-label="Close navigation" type="button" data-testid="button-close-navigation" className="fixed inset-0 z-30 bg-[#101621]/30 lg:hidden" onClick={() => setRailOpen(false)} />}
      <div className={`transition-[padding] duration-300 ${railOpen ? 'lg:pl-[248px]' : 'lg:pl-0'}`}>
        <header className="sticky top-0 z-20 flex h-[74px] items-center justify-between border-b border-border/80 bg-background/95 px-5 backdrop-blur-md md:px-8">
          <div className="flex items-center gap-3">
            <button type="button" data-testid="button-open-navigation" onClick={() => setRailOpen((v) => !v)} className="rounded-md p-2 text-muted-foreground hover:bg-muted">{railOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}</button>
            <div className="hidden items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground sm:flex"><LayoutDashboard size={14} /> Underwriting workspace <span className="text-border">/</span> <span className="text-foreground">{location === '/memo' ? 'Narrative studio' : 'Document intake'}</span></div>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" data-testid="button-global-search" onClick={() => setSearchOpen(true)} className="hidden items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-left text-xs text-muted-foreground shadow-sm transition-colors hover:border-foreground/25 sm:flex"><Search size={14} /> Search workspace <span className="ml-4 font-mono text-[9px] opacity-50">⌘ K</span></button>
            <button type="button" data-testid="button-notifications" className="relative rounded-md p-2.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"><Bell size={17} /><span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-[#d29439]" /></button>
            <div className="hidden h-7 w-px bg-border sm:block" />
            <div className="hidden items-center gap-2 text-xs md:flex"><span className="h-2 w-2 rounded-full bg-[#4f967d]" /> Autosaved</div>
          </div>
        </header>
        <main>{children}</main>
      </div>
      <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
        <DialogContent className="overflow-hidden p-0 data-[state=open]:slide-in-from-right-1/2 data-[state=open]:slide-in-from-top-[10%] data-[state=closed]:slide-out-to-right-1/2 data-[state=closed]:slide-out-to-top-[10%]">
          <Command className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group]:not([hidden])_~[cmdk-group]]:pt-0 [&_[cmdk-group]]:px-2 [&_[cmdk-input-wrapper]_svg]:h-5 [&_[cmdk-input-wrapper]_svg]:w-5 [&_[cmdk-input]]:h-12 [&_[cmdk-item]]:px-2 [&_[cmdk-item]]:py-3 [&_[cmdk-item]_svg]:h-5 [&_[cmdk-item]_svg]:w-5">
            <CommandInput placeholder="Search pages..." />
            <CommandList>
              <CommandEmpty>No page found.</CommandEmpty>
              <CommandGroup heading="Pages">
                {navItems.map(({ href, label, detail, icon: Icon, keywords }) => (
                  <CommandItem
                    key={href}
                    value={`${label} ${detail} ${keywords.join(' ')}`}
                    onSelect={() => {
                      navigate(href);
                      setSearchOpen(false);
                    }}
                  >
                    <Icon size={16} />
                    <span>{label}</span>
                    <span className="ml-auto text-xs text-muted-foreground">{detail}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </DialogContent>
      </Dialog>
    </div>
  );
}
