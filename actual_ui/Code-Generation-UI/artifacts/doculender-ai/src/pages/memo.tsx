import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Circle, Download, Eye, FileText, Loader2, LockKeyhole, MessageSquare, Play, RefreshCw, Send, Sparkles, Terminal } from 'lucide-react';
import { useApproveMemoSection, useFinalizeMemo, useGetMemoStatus, getGetMemoStatusQueryKey, useGetIndexerStatus, getGetIndexerStatusQueryKey } from '@workspace/api-client-react';
import type { MemoSection, MemoState } from '@workspace/api-client-react';
import { useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { documentMarkdownComponents, renderMarkdownWithReasoning, terminalMarkdownComponents } from '@/components/markdown-renderer';
import { RequestChatWidget } from '@/components/request-chat-widget';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { useRequestId } from '@/lib/request-id-context';

const demoSections: MemoSection[] = [];
const demoMemo: MemoState = { requestId: '', status: 'drafting', approvedCount: 0, totalSections: 0, sections: demoSections, rationales: [] };

export default function Memo() {
  const { requestId: sharedRequestId } = useRequestId();
  const [requestId, setRequestId] = useState('');
  const [submittedId, setSubmittedId] = useState('');
  const [memo, setMemo] = useState<MemoState>(demoMemo);
  const [terminal, setTerminal] = useState<string[]>(['doculender agent / v2.8.1', 'Awaiting request ID for narrative workflow...']);
  const [streaming, setStreaming] = useState(false);
  const [finalized, setFinalized] = useState<{ blobPath: string; markdown: string } | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [terminalOpen, setTerminalOpen] = useState(true);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const [sectionNotes, setSectionNotes] = useState<Record<string, string>>({});
  const [regeneratingSection, setRegeneratingSection] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const pendingRef = useRef<Record<string, string>>({});
  const rafRef = useRef<number | null>(null);
  const flushContent = useCallback(() => {
    const pending = pendingRef.current;
    pendingRef.current = {};
    const keys = Object.keys(pending);
    if (keys.length > 0) {
      setMemo(prev => {
        const updated = { ...prev, sections: prev.sections.map(s => pending[s.name] ? { ...s, content: (s.content || '') + pending[s.name] } : s) };
        return updated;
      });
    }
    rafRef.current = null;
  }, []);
  const sharedIndexer = useGetIndexerStatus(
    { requestId: sharedRequestId },
    {
      query: {
        queryKey: getGetIndexerStatusQueryKey({ requestId: sharedRequestId }),
        enabled: Boolean(sharedRequestId),
        refetchInterval: (q) => {
          const s = q.state.data?.indexingStatus?.toLowerCase();
          return s === 'succeeded' || s === 'failed' ? false : 5000;
        },
      },
    },
  );
  const sharedIndexStatus = (sharedIndexer.data?.indexingStatus || '').toLowerCase();
  const statusQuery = useGetMemoStatus({ requestId: submittedId }, { query: { queryKey: getGetMemoStatusQueryKey({ requestId: submittedId }), enabled: Boolean(submittedId) } });
  const approve = useApproveMemoSection();
  const finalize = useFinalizeMemo();
  const serverMemo = statusQuery.data;
  const activeMemo = memo;

  useEffect(() => {
    if (serverMemo?.sections?.length && !memo.sections.length) {
      setMemo(serverMemo);
    }
  }, [serverMemo]);

  useEffect(() => {
    if (!sharedRequestId) return;
    if (requestId && requestId !== sharedRequestId) return;
    setRequestId(sharedRequestId);
  }, [sharedRequestId, requestId]);

  useEffect(() => {
    if (!sharedRequestId || sharedIndexStatus !== 'succeeded') return;
    if (submittedId === sharedRequestId) return;
    if ((requestId && requestId !== sharedRequestId) || (submittedId && submittedId !== sharedRequestId)) return;

    setRequestId(sharedRequestId);
    setSubmittedId(sharedRequestId);
    setFinalized(null);
    setTerminal((lines) => [...lines.slice(-4), `Request loaded: ${sharedRequestId}`, 'Indexing complete. Ready to draft memo narrative.']);
  }, [sharedRequestId, sharedIndexStatus, requestId, submittedId]);

  const counts = useMemo(() => ({ approved: activeMemo.sections.filter((section) => section.status === 'approved').length, total: activeMemo.sections.length }), [activeMemo.sections]);
  const isAnyStreaming = streaming || Boolean(regeneratingSection);

  const toggleSectionExpanded = (name: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const loadRequest = () => {
    if (!requestId.trim()) return;
    setSubmittedId(requestId.trim());
    setTerminal((lines) => [...lines.slice(-4), `Request loaded: ${requestId.trim()}`, 'Awaiting narrative agent stream\u2026']);
    setFinalized(null);
  };

  const startDraft = async () => {
    if (!submittedId) {
      setTerminal(t => [...t, 'Error: Please load a request ID first.']);
      return;
    }
    setStreaming(true);
    setMemo((current) => ({ ...current, status: 'drafting' }));
    setTerminal(['doculender agent / v2.8.1', `Connecting to stream for ${submittedId}\u2026`]);

    try {
      const response = await fetch(`/api/memo/stream?requestId=${submittedId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requestId: submittedId })
      });

      if (!response.body) throw new Error('No stream body');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6));
              const evt = payload.event;

              if (evt === 'analysis_complete') {
                setTerminal(t => [...t, `Analysis complete. Found ${payload.active_sections.length} applicable sections.`]);
                setMemo(current => ({ ...current, sections: payload.active_sections }));
              } else if (evt === 'section_start') {
                setTerminal(t => [...t, `Drafting section: ${payload.section_name}\u2026`]);
              } else if (evt === 'tool_call') {
                setTerminal(t => [...t, `\uD83D\uDD27 Executing ${payload.tool} query: "${payload.query}"`]);
              } else if (evt === 'token_delta') {
                pendingRef.current[payload.section_name] = (pendingRef.current[payload.section_name] || '') + payload.delta;
                if (!rafRef.current) {
                  rafRef.current = requestAnimationFrame(flushContent);
                }
              } else if (evt === 'reasoning_delta') {
                pendingRef.current[payload.section_name] = (pendingRef.current[payload.section_name] || '') + payload.delta;
                if (!rafRef.current) {
                  rafRef.current = requestAnimationFrame(flushContent);
                }
                if (Math.random() < 0.1) {
                  setTerminal(t => [...t, `\uD83D\uDCAD Reasoning: ${payload.delta.slice(0, 60)}${payload.delta.length > 60 ? '\u2026' : ''}`]);
                }
              } else if (evt === 'terminal_token') {
                setTerminal(t => {
                  const last = t[t.length - 1] || '';
                  if (last.startsWith(`\u270D\uFE0F ${payload.section_name}: `)) {
                    return [...t.slice(0, -1), last + payload.token];
                  }
                  return [...t, `\u270D\uFE0F ${payload.section_name}: ${payload.token}`];
                });
              } else if (evt === 'section_complete') {
                if (rafRef.current) {
                  cancelAnimationFrame(rafRef.current);
                  rafRef.current = null;
                }
                flushContent();
                setTerminal(t => [...t, `\u2713 Completed: ${payload.section_name}`]);
              } else if (evt === 'flow_complete') {
                if (rafRef.current) {
                  cancelAnimationFrame(rafRef.current);
                  rafRef.current = null;
                }
                flushContent();
                setMemo(payload.memo);
                setTerminal(t => [...t, 'Review state: ready_for_review']);
              }
            } catch (e) {
              console.error('Failed to parse SSE payload', e);
            }
          }
        }
      }
    } catch (e) {
      console.error(e);
      setTerminal(t => [...t, 'Error: Stream disconnected.']);
    } finally {
      setStreaming(false);
    }
  };

  const updateSection = (next: MemoSection) => setMemo((current) => ({ ...current, approvedCount: current.sections.filter((section) => section.name === next.name ? next.status === 'approved' : section.status === 'approved').length, sections: current.sections.map((section) => section.name === next.name ? next : section) }));
  const approveSection = (section: MemoSection) => {
    updateSection({ ...section, status: 'approved' });
    approve.mutate({ data: { requestId: submittedId, sectionName: section.name } }, { onSuccess: (data) => { if (data?.sections?.length) setMemo(data); }, onError: () => updateSection({ ...section, status: 'approved' }) });
  };
  const regenerateSection = async (section: MemoSection) => {
    if (!submittedId || regeneratingSection) return;
    const note = sectionNotes[section.name] || '';
    const previous = section;

    setRegeneratingSection(section.name);
    updateSection({ ...section, content: '', status: 'regenerating', reviewerNotes: note });
    setTerminal((lines) => [...lines.slice(-10), `Regenerating section: ${section.name}\u2026`]);

    try {
      const response = await fetch('/api/memo/section/regenerate/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requestId: submittedId, sectionName: section.name, reviewerNotes: note })
      });

      if (!response.ok) {
        throw new Error(`Regenerate stream failed: HTTP ${response.status}`);
      }
      if (!response.body) throw new Error('No stream body');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const payload = JSON.parse(line.slice(6));
            const evt = payload.event;

            if (evt === 'regen_started') {
              setTerminal((t) => [...t, `Connected. Streaming regeneration for ${payload.section_name}\u2026`]);
            } else if (evt === 'status') {
              setTerminal((t) => [...t, payload.message || `Regenerating ${payload.section_name}\u2026`]);
            } else if (evt === 'token_delta') {
              pendingRef.current[payload.section_name] = (pendingRef.current[payload.section_name] || '') + payload.delta;
              if (!rafRef.current) {
                rafRef.current = requestAnimationFrame(flushContent);
              }
            } else if (evt === 'terminal_token') {
              setTerminal((t) => {
                const last = t[t.length - 1] || '';
                if (last.startsWith(`\u270D\uFE0F ${payload.section_name}: `)) {
                  return [...t.slice(0, -1), last + payload.token];
                }
                return [...t, `\u270D\uFE0F ${payload.section_name}: ${payload.token}`];
              });
            } else if (evt === 'regen_complete') {
              if (rafRef.current) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
              }
              flushContent();
              updateSection({ ...section, status: 'drafted', content: payload.content || '', regenCount: payload.regen_count ?? (section.regenCount + 1), reviewerNotes: note });
              setTerminal((t) => [...t, `\u2713 Regenerated: ${payload.section_name}`]);
              setSectionNotes(prev => ({ ...prev, [section.name]: '' }));
            } else if (evt === 'manual_escalation') {
              if (rafRef.current) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
              }
              flushContent();
              updateSection({ ...section, status: 'manual_escalation', content: previous.content, regenCount: payload.regen_count ?? section.regenCount, reviewerNotes: note || payload.message });
              setTerminal((t) => [...t, `Manual escalation for ${payload.section_name}: ${payload.message}`]);
            } else if (evt === 'regen_error') {
              if (rafRef.current) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
              }
              flushContent();
              updateSection(previous);
              setTerminal((t) => [...t, `Error regenerating ${payload.section_name || section.name}: ${payload.message || 'unknown error'}`]);
            }
          } catch (error) {
            console.error('Failed to parse regenerate SSE payload', error);
          }
        }
      }
    } catch (error) {
      console.error(error);
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      flushContent();
      updateSection(previous);
      setTerminal((t) => [...t, `Error: Regenerate stream disconnected for ${section.name}.`]);
    } finally {
      setRegeneratingSection(null);
    }
  };
  const buildMemoMarkdown = useCallback(() => {
    const sections = activeMemo.sections.filter(s => s.content);
    if (!sections.length) return '# Credit Memo Narrative\n\n*No narrative drafted.*';
    const body = sections.map(s => {
      const content = s.content.trim();
      const hasHeading = /^#{1,3}\s/.test(content);
      return hasHeading ? content : `## ${s.name}\n\n${content}`;
    }).join('\n\n---\n\n');
    return `# Credit Memo Narrative\n\n**Request ID:** ${submittedId}\n\n---\n\n${body}\n\n---\n\n*Credit memo narrative generated by DocuLender AI · ${new Date().toISOString().slice(0, 10)}*`;
  }, [activeMemo.sections, submittedId]);
  const finalizeMemo = () => finalize.mutate({ data: { requestId: submittedId, approver: 'Morgan Chen' } }, { onSuccess: (data) => setFinalized({ ...data, markdown: data?.markdown || buildMemoMarkdown() }), onError: () => setFinalized({ blobPath: `memos/${submittedId}/final.md`, markdown: buildMemoMarkdown() }) });

  return (
    <div className="mx-auto max-w-[1440px] px-5 pb-16 pt-8 md:px-8 lg:px-10">
      <div className="mb-8 flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
        <div><div className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground"><span className="h-1.5 w-1.5 rounded-full bg-[#d29439]" /> Narrative studio <span className="mx-1.5 inline-block h-0.5 w-0.5 rounded-full bg-border align-middle" /> human-in-the-loop</div><h1 className="font-serif text-[clamp(2.5rem,5vw,4.3rem)] leading-[0.92] tracking-[-0.04em]">Credit memo narratives,<br /><em className="text-[#a17734]">with receipts.</em></h1><p className="mt-4 max-w-xl text-sm leading-6 text-muted-foreground">Let the agent assemble narrative evidence. You decide what earns a place in the final recommendation.</p></div>
        <div className="flex items-center gap-3 rounded-md border border-border bg-card p-2 shadow-sm"><div className="grid h-9 w-9 place-items-center rounded bg-[#e8e0ca] text-[#84632c]"><FileText size={17} /></div><div className="pr-3"><div className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">Active request</div><div className="mt-0.5 text-xs font-semibold">{submittedId}</div></div><div className="h-7 w-px bg-border" /><div className="px-2 text-right"><div className="font-serif text-xl leading-none">{counts.approved}<span className="font-sans text-sm text-muted-foreground">/{counts.total}</span></div><div className="mt-1 font-mono text-[8px] uppercase tracking-[0.1em] text-muted-foreground">narratives approved</div></div></div>
      </div>
      <div className="mb-5 flex flex-col gap-3 rounded-lg border border-border bg-card p-4 md:flex-row md:items-center">
        <div className="flex flex-1 items-center gap-3"><span className="font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">Request ID</span><input value={requestId} onChange={(event) => setRequestId(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && loadRequest()} className="h-9 min-w-0 flex-1 rounded border border-input bg-background px-3 font-mono text-xs outline-none focus:border-[#a17734] focus:ring-2 focus:ring-[#a17734]/15" data-testid="input-request-id" /></div><button type="button" onClick={loadRequest} data-testid="button-load-request" className="flex h-9 items-center justify-center gap-2 rounded bg-secondary px-4 text-xs font-semibold transition-colors hover:bg-muted"><RefreshCw size={14} /> Load request</button><button type="button" onClick={startDraft} disabled={!submittedId || isAnyStreaming} data-testid="button-start-agent" className="flex h-9 items-center justify-center gap-2 rounded bg-primary px-4 text-xs font-semibold text-primary-foreground disabled:opacity-50"><Play size={13} /> {streaming ? 'Agent drafting narratives\u2026' : regeneratingSection ? 'Narrative regenerating\u2026' : 'Draft narrative with agent'}</button></div>
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,.84fr)_minmax(0,1.5fr)_minmax(300px,.72fr)]">
        <section className="self-start rounded-lg border border-border bg-[#1d2730] text-[#e9e5db] shadow-[0_10px_30px_-20px_rgba(20,30,35,.7)]">
          <button type="button" onClick={() => setTerminalOpen(v => !v)} className="flex w-full items-center justify-between border-b border-[#39454b] px-5 py-4 text-left">
            <div className="flex items-center gap-2 text-sm font-semibold"><Terminal size={16} className="text-[#d7bd70]" /> Narrative stream</div>
            <div className="flex items-center gap-3"><div className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.1em] text-[#9eabb0]"><span className={`h-1.5 w-1.5 rounded-full ${isAnyStreaming ? 'animate-pulse bg-[#d7bd70]' : 'bg-[#6ea184]'}`} /> {isAnyStreaming ? 'Writing' : 'Idle'}</div>{terminalOpen ? <ChevronDown size={14} className="text-[#9eabb0]" /> : <ChevronRight size={14} className="text-[#9eabb0]" />}</div>
          </button>
          {terminalOpen && (
            <>
              <div className="p-5">
                <div className="mb-3 font-mono text-[10px] text-[#8f9da2]"># request/{submittedId}</div>
                <div className="rounded border border-[#3d4a51] bg-[#192229] p-3 font-mono text-[10px] leading-6">
                  {terminal.map((line, index) => {
                    const marker = line.indexOf(': ');
                    const isDraftLine = line.startsWith('✍️ ') && marker > -1;
                    if (isDraftLine) {
                      const header = line.slice(0, marker + 1);
                      const markdown = line.slice(marker + 2);
                      return (
                        <div key={`${line}-${index}`} className="mb-3 rounded border border-[#3f4c52] bg-[#212d34] p-2 text-[#d6d8d2]">
                          <div className="mb-1"><span className="mr-2 text-[#68777c]">{String(index + 1).padStart(2, '0')}</span><span className="text-[#9fb0b7]">{header}</span></div>
                          <ReactMarkdown remarkPlugins={[remarkGfm]} components={terminalMarkdownComponents}>{markdown || '_...'}</ReactMarkdown>
                        </div>
                      );
                    }
                    return <div key={`${line}-${index}`} className={`${line.includes('ready') ? 'text-[#d7bd70]' : line.includes('Connected') || line.includes('assembled') ? 'text-[#8cc49a]' : 'text-[#d6d8d2]'}`}><span className="mr-2 text-[#68777c]">{String(index + 1).padStart(2, '0')}</span>{line}</div>;
                  })}
                  {isAnyStreaming && <div className="mt-2 flex items-center gap-2 text-[#d7bd70]"><span className="inline-block h-3 w-2 animate-pulse rounded-sm bg-[#d7bd70] align-middle" /> {regeneratingSection ? `regenerating ${regeneratingSection}` : 'agent is thinking'}</div>}
                </div>
              </div>
              <div className="border-t border-[#39454b] p-4"><div className="flex items-center gap-2 rounded border border-[#45525a] bg-[#26323a] px-3 py-2 text-[10px] text-[#9eabb0]"><Sparkles size={13} className="text-[#d7bd70]" /> Evidence-linked narrative drafting enabled</div></div>
            </>
          )}
        </section>
        <section className="rounded-lg border border-border bg-card">
          <div className="border-b border-border px-5 py-4"><div className="flex items-center justify-between"><div><div className="text-sm font-semibold">Narrative sections</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">Review each narrative section before sign-off</div></div><span className={`rounded-full px-2 py-1 font-mono text-[9px] uppercase tracking-[0.08em] ${activeMemo.status === 'ready_for_review' ? 'bg-[#f1e6c9] text-[#916a2a]' : 'bg-muted text-muted-foreground'}`}>{activeMemo.status.replaceAll('_', ' ')}</span></div></div>
          <div className="divide-y divide-border">
            {activeMemo.sections.map((section, index) => {
              const expanded = expandedSections.has(section.name);
              return (
                <div key={section.name}>
                  <button type="button" onClick={() => toggleSectionExpanded(section.name)} data-testid={`button-section-${index}`} className="flex w-full items-center gap-2 px-5 py-3 text-left transition-colors hover:bg-muted">
                    <StatusIcon status={section.status} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-xs font-semibold">{section.name}</span>
                      <span className="mt-1 block font-mono text-[9px] uppercase tracking-[0.06em] text-muted-foreground">{section.status === 'manual_escalation' ? 'manual review' : section.status}</span>
                    </span>
                    <span className="text-muted-foreground/50">{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
                  </button>
                  {expanded && (
                    <div className="border-t border-border px-5 py-5">
                      <div className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
                        <span className="rounded bg-muted px-2 py-1">{section.status.replaceAll('_', ' ')}</span>
                        {section.regenCount > 0 && <span>Regenerated {section.regenCount}x</span>}
                      </div>
                      <div className="mt-4 rounded-md border border-[#e1d9c9] bg-[#fbf8f1] p-5 text-sm leading-7 text-foreground/85">{renderMarkdownWithReasoning(section.content)}</div>
                      {section.status === 'manual_escalation' && (
                        <div className="mt-4 flex gap-2 rounded-md border border-[#e7d3ad] bg-[#f9f1df] p-3 text-xs leading-5 text-[#88642e]">
                          <AlertCircle size={15} className="mt-0.5 shrink-0" />
                          <span><strong>Manual escalation:</strong> {section.reviewerNotes}</span>
                        </div>
                      )}
                      <div className="mt-4">
                        <label className="mb-2 flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground"><MessageSquare size={12} /> Reviewer notes <span className="normal-case tracking-normal opacity-60">optional</span></label>
                        <textarea value={sectionNotes[section.name] || ''} onChange={(event) => setSectionNotes(prev => ({ ...prev, [section.name]: event.target.value }))} placeholder="Add guidance for the next narrative draft\u2026" className="min-h-[60px] w-full resize-none rounded-md border border-input bg-background p-3 text-xs outline-none placeholder:text-muted-foreground/65 focus:border-[#a17734]" />
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <button type="button" onClick={() => approveSection(section)} disabled={section.status === 'approved' || section.status === 'regenerating' || Boolean(regeneratingSection) || approve.isPending} data-testid="button-approve-section" className="flex items-center gap-2 rounded bg-[#39745e] px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-[#274f41] disabled:opacity-50"><CheckCircle2 size={14} /> {section.status === 'approved' ? 'Approved' : 'Approve'}</button>
                        <button type="button" onClick={() => regenerateSection(section)} disabled={Boolean(regeneratingSection) || section.status === 'manual_escalation'} data-testid="button-regenerate-section" className="flex items-center gap-2 rounded border border-border px-3.5 py-2 text-xs font-semibold transition-colors hover:bg-muted disabled:opacity-50"><RefreshCw size={14} className={regeneratingSection === section.name ? 'animate-spin' : ''} /> {regeneratingSection === section.name ? 'Regenerating narrative\u2026' : 'Regenerate narrative'}</button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
        <aside className="space-y-5">
          <RequestChatWidget requestId={submittedId} />
          <section className="rounded-lg border border-border bg-card p-5"><div className="flex items-center gap-2 text-sm font-semibold"><Sparkles size={16} className="text-[#a17734]" /> Narrative rationale</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">Why the agent prioritized these narrative threads</div><div className="mt-5 space-y-4">{(activeMemo.rationales?.length ? activeMemo.rationales : activeMemo.sections.map((s) => (s as { rationale?: string }).rationale).filter(Boolean)).map((rationale, index) => <div key={rationale} className="flex gap-3 text-xs leading-5 text-foreground/70"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#f1e6c9] font-mono text-[9px] text-[#916a2a]">{String(index + 1).padStart(2, '0')}</span><span>{rationale}</span></div>)}</div></section>
          <section className="rounded-lg border border-border bg-[#e7eee8] p-5"><div className="flex items-center gap-2 text-sm font-semibold text-[#274f41]"><LockKeyhole size={16} /> Final sign-off</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-[#39745e]/70">All narrative sections require approval</div><div className="mt-5 flex items-end justify-between"><div className="font-serif text-4xl text-[#274f41]">{counts.approved}<span className="font-sans text-lg text-[#39745e]/50">/{counts.total}</span></div><div className="text-right text-xs text-[#39745e]/75">{counts.approved === counts.total ? 'Ready to publish' : `${counts.total - counts.approved} section${counts.total - counts.approved === 1 ? '' : 's'} to review`}</div></div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#cadbcf]"><div className="h-full rounded-full bg-[#39745e] transition-all duration-500" style={{ width: `${(counts.approved / Math.max(counts.total, 1)) * 100}%` }} /></div><button type="button" onClick={finalizeMemo} disabled={counts.approved !== counts.total || finalize.isPending || Boolean(finalized)} data-testid="button-finalize-memo" className="mt-5 flex w-full items-center justify-center gap-2 rounded bg-[#274f41] px-3 py-3 text-xs font-semibold text-white transition-colors hover:bg-[#1c3b31] disabled:cursor-not-allowed disabled:opacity-40">{finalize.isPending ? <Loader2 className="animate-spin" size={14} /> : finalized ? <CheckCircle2 size={14} /> : <Send size={14} />} {finalized ? 'Published to workspace' : 'Finalize & publish'}</button>{finalized && <div className="mt-3 break-all font-mono text-[9px] uppercase tracking-[0.08em] text-[#39745e]/75">{finalized.blobPath}</div>}{finalized && <div className="mt-3 flex gap-2"><button type="button" onClick={() => setPreviewContent(finalized.markdown)} className="flex flex-1 items-center justify-center gap-2 rounded border border-[#39745e]/30 px-3 py-2 text-xs font-semibold text-[#274f41] transition-colors hover:bg-[#274f41]/10"><Eye size={14} /> Preview</button><button type="button" onClick={() => { const blob = new Blob([finalized.markdown], { type: 'text/markdown' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `memo-${submittedId}.md`; a.click(); URL.revokeObjectURL(url); }} className="flex flex-1 items-center justify-center gap-2 rounded border border-[#39745e]/30 px-3 py-2 text-xs font-semibold text-[#274f41] transition-colors hover:bg-[#274f41]/10"><Download size={14} /> Download .md</button></div>}</section>
          <Dialog open={Boolean(previewContent)} onOpenChange={(open) => { if (!open) setPreviewContent(null); }}>
            <DialogContent className="max-w-4xl max-h-[90vh] flex flex-col gap-0 p-0">
              <DialogTitle className="sr-only">Final Credit Memo Narrative</DialogTitle>
              <div className="flex items-center justify-between border-b border-border px-5 py-3">
                <div className="flex items-center gap-2 text-sm font-semibold"><FileText size={15} className="text-[#39745e]" /> Final Credit Memo Narrative</div>
                <span className="font-mono text-[10px] text-muted-foreground">{submittedId}</span>
              </div>
              <div className="flex-1 overflow-auto p-6 text-sm leading-7 text-foreground/85">
                <div>
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={documentMarkdownComponents}>{previewContent || ''}</ReactMarkdown>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </aside>
      </div>
    </div>
  );
}

function StatusIcon({ status }: { status: MemoSection['status'] }) {
  if (status === 'approved') return <CheckCircle2 size={15} className="shrink-0 text-[#39745e]" />;
  if (status === 'manual_escalation') return <AlertCircle size={15} className="shrink-0 text-[#b07831]" />;
  return <Circle size={15} className="shrink-0 text-[#a17734]" />;
}
