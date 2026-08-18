import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { AlertCircle, Bot, ChevronDown, Loader2, MessageSquare, Send, Sparkles } from 'lucide-react';
import { useGetIndexerStatus, getGetIndexerStatusQueryKey } from '@workspace/api-client-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { documentMarkdownComponents } from '@/components/markdown-renderer';

type ChatRole = 'user' | 'assistant';

type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  status: 'complete' | 'streaming' | 'error';
  activity?: string[];
};

const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

type RequestChatWidgetProps = {
  requestId?: string;
  mode?: 'request' | 'portfolio';
};

export function RequestChatWidget({ requestId = '', mode = 'request' }: RequestChatWidgetProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const endRef = useRef<HTMLDivElement | null>(null);
  const indexer = useGetIndexerStatus(
    { requestId },
    {
      query: {
        queryKey: getGetIndexerStatusQueryKey({ requestId }),
        enabled: Boolean(requestId),
        refetchInterval: (q) => {
          const status = q.state.data?.indexingStatus?.toLowerCase();
          return status === 'succeeded' || status === 'failed' ? false : 5000;
        },
      },
    },
  );

  const indexStatus = indexer.data?.indexingStatus ?? (requestId ? 'pending' : 'idle');
  const indexMessage = indexer.data?.indexingErrors?.length ? indexer.data.indexingErrors.join('; ') : undefined;
  const portfolioMode = mode === 'portfolio';

  useEffect(() => {
    setMessages([]);
    setDraft('');
    setError('');
    setSending(false);
  }, [requestId, mode]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, sending]);

  const sendMessage = async () => {
    const text = draft.trim();
    if ((!portfolioMode && !requestId) || !text || sending) return;

    setError('');
    setDraft('');

    const userMessage: ChatMessage = {
      id: uid(),
      role: 'user',
      content: text,
      status: 'complete',
    };
    const assistantId = uid();
    const assistantPlaceholder: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      status: 'streaming',
      activity: [portfolioMode ? 'Starting portfolio reporting assistant...' : 'Starting request-scoped assistant...'],
    };

    const appendAssistantActivity = (line: string) => {
      setMessages((current) =>
        current.map((message) => {
          if (message.id !== assistantId) return message;
          const nextActivity = [...(message.activity || []), line].slice(-8);
          return { ...message, activity: nextActivity };
        }),
      );
    };

    const history = [...messages, userMessage].map((message) => ({ role: message.role, content: message.content }));

    setMessages((current) => [...current, userMessage, assistantPlaceholder]);
    setSending(true);

    try {
      const response = await fetch(portfolioMode ? '/api/portfolio-reporting/stream' : '/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(portfolioMode ? { message: text, history } : { requestId, message: text, history }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error || `Unable to start ${portfolioMode ? 'portfolio reporting' : 'request-scoped'} chat.`);
      }

      if (!response.body) throw new Error('No response stream available.');

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

            if (evt === 'chat_start') {
              appendAssistantActivity(`Connected to stream ${payload.streamId || ''}`.trim());
              continue;
            }

            if (evt === 'status') {
              if (payload?.message) {
                appendAssistantActivity(String(payload.message));
              }
              continue;
            }

            if (evt === 'tool_call') {
              appendAssistantActivity(`Tool call: ${payload.tool || 'search'} -> "${payload.query || ''}"`);
              continue;
            }

            if (evt === 'tool_result') {
              appendAssistantActivity(`Tool result: ${payload.hitCount ?? 0} hit(s) in ${payload.latencyMs ?? 0} ms`);
              continue;
            }

            if (evt === 'token_delta') {
              const delta = (payload.delta || '').replace(/\u258B/g, '');
              if (!delta) continue;
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId ? { ...message, content: message.content + delta } : message,
                ),
              );
              continue;
            }

            if (evt === 'chat_complete') {
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId
                    ? {
                        ...message,
                        content: payload.content || message.content,
                        status: 'complete',
                        activity: [...(message.activity || []), 'Answer completed.'].slice(-8),
                      }
                    : message,
                ),
              );
              continue;
            }

            if (evt === 'error') {
              const messageText = payload.message || `The ${portfolioMode ? 'portfolio reporting' : 'request-scoped'} chat could not answer right now.`;
              setError(messageText);
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId
                    ? {
                        ...message,
                        content: messageText,
                        status: 'error',
                        activity: [...(message.activity || []), `Error: ${messageText}`].slice(-8),
                      }
                    : message,
                ),
              );
            }
          } catch (parseError) {
            console.error('Failed to parse chat SSE payload', parseError);
          }
        }
      }
    } catch (err) {
      const messageText = err instanceof Error ? err.message : 'Chat stream disconnected.';
      setError(messageText);
      setMessages((current) =>
        current.map((message) =>
          message.role === 'assistant' && message.status === 'streaming'
            ? { ...message, content: messageText, status: 'error' }
            : message,
        ),
      );
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  };

  return (
    <section className="rounded-lg border border-border bg-card p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold">
            <MessageSquare size={16} className="text-[#a17734]" /> {portfolioMode ? 'Portfolio reporting' : 'Narrative chat'}
          </div>
          <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">
            {portfolioMode ? 'Portfolio-wide SQL reporting' : 'Scoped to this narrative request only'}
          </div>
        </div>
        {portfolioMode ? (
          <span className="rounded-full bg-[#d0e2d5] px-2 py-1 font-mono text-[9px] uppercase tracking-[0.1em] text-[#39745e]">portfolio</span>
        ) : (
          <span
            className={`rounded-full px-2 py-1 font-mono text-[9px] uppercase tracking-[0.1em] ${
              indexStatus === 'succeeded'
                ? 'bg-[#d0e2d5] text-[#39745e]'
                : indexStatus === 'running'
                  ? 'bg-amber-100 text-amber-700'
                  : 'bg-muted text-muted-foreground'
            }`}
          >
            {indexStatus}
          </span>
        )}
      </div>

      <div className="mt-4 rounded-md border border-dashed border-border bg-[#faf8f2] px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        <div className="flex items-center gap-2 font-medium text-foreground">
          <Sparkles size={13} className="text-[#d29439]" />
          {portfolioMode ? 'Read-only operational reporting' : 'Evidence-linked narrative answers only'}
        </div>
        <div className="mt-1">
          {portfolioMode
            ? 'Ask cross-portfolio questions about cases, monitoring, conditions, relationships, and collateral controls.'
            : requestId
              ? `Working on narrative request ${requestId}.`
              : 'Load a request to start chatting with indexed narrative evidence.'}
        </div>
        {!portfolioMode && indexMessage ? <div className="mt-1 text-[#8d6a2f]">{indexMessage}</div> : null}
      </div>

      <div className="mt-4 max-h-[360px] space-y-3 overflow-auto rounded-md border border-border bg-[#f9f7f3] p-3">
        {messages.length === 0 ? (
          <div className="rounded-md border border-dashed border-border bg-white px-4 py-8 text-center text-xs text-muted-foreground">
            {portfolioMode
              ? 'Ask about pipeline volume, watchlist status, overdue conditions, or collateral control exceptions across the portfolio.'
              : 'Ask about borrower details, terms, collateral, or any other evidence for the memo narrative in this request.'}
          </div>
        ) : null}

        {messages.map((message) => (
          <div key={message.id} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[92%] rounded-lg border px-3 py-2 text-sm leading-6 shadow-sm ${
                message.role === 'user'
                  ? 'border-[#d9ccb2] bg-[#f0e4cf] text-[#503a1a]'
                  : message.status === 'error'
                    ? 'border-[#e2bf8e] bg-[#fff7eb] text-[#6d4c19]'
                    : 'border-border bg-white text-foreground'
              }`}
            >
              <div className="mb-1 flex items-center justify-between gap-3 text-[9px] font-mono uppercase tracking-[0.12em] text-muted-foreground">
                <span>{message.role === 'user' ? 'You' : 'Assistant'}</span>
                {message.role === 'assistant' ? (
                  <span className={message.status === 'streaming' ? 'text-[#a17734]' : 'text-muted-foreground'}>
                    {message.status === 'streaming' ? 'Thinking' : message.status === 'error' ? 'Needs retry' : 'Ready'}
                  </span>
                ) : null}
              </div>

              {message.role === 'assistant' ? (
                <>
                  {message.activity?.length ? (
                    <details open={message.status === 'streaming'} className="mb-2 rounded border border-[#e3d7be] bg-[#f9f3e5] px-2.5 py-1.5">
                      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 font-mono text-[9px] uppercase tracking-[0.11em] text-[#7e6332]">
                        <span className="flex items-center gap-1.5">
                          <Bot size={12} className="text-[#8a6a35]" />
                          Thinking trace
                        </span>
                        <span className="flex items-center gap-1 text-[8px] tracking-[0.09em] text-[#977848]">
                          Expand for steps
                          <ChevronDown size={11} />
                        </span>
                      </summary>
                      <div
                        className={`mt-1.5 space-y-1 text-[11px] leading-5 text-[#6f5730] ${
                          message.status === 'streaming' ? '[&>div]:[animation:thinkingPulse_3s_ease-in-out_infinite]' : ''
                        }`}
                      >
                        {message.activity.map((line, idx) => (
                          <div key={`${line}-${idx}`}>{line}</div>
                        ))}
                      </div>
                    </details>
                  ) : null}
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={documentMarkdownComponents}>
                    {message.content || (message.status === 'streaming' ? '...' : '_No answer yet._')}
                  </ReactMarkdown>
                </>
              ) : (
                <div className="whitespace-pre-wrap">{message.content}</div>
              )}
            </div>
          </div>
        ))}

        <div ref={endRef} />
      </div>

      {error ? (
        <div className="mt-3 flex gap-2 rounded-md border border-[#e6c99c] bg-[#fff8ec] px-3 py-2 text-xs leading-5 text-[#8a5e18]">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="mt-4 space-y-3">
        <Textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={portfolioMode ? 'Ask a portfolio reporting question…' : requestId ? 'Ask a question about narrative evidence…' : 'Load a request first…'}
          disabled={(!portfolioMode && !requestId) || sending}
          className="min-h-[94px] resize-none bg-white text-sm"
        />

        <div className="flex items-center justify-between gap-3">
          <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">
            {portfolioMode ? 'Portfolio reporting' : requestId ? `Request ${requestId}` : 'No active request'}
          </div>
          <Button type="button" onClick={() => void sendMessage()} disabled={(!portfolioMode && !requestId) || !draft.trim() || sending} size="sm">
            {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            {sending ? 'Sending' : 'Send'}
          </Button>
        </div>
      </div>
      <style>{`@keyframes thinkingPulse { 0%, 100% { opacity: 0.42; } 50% { opacity: 1; } }`}</style>
    </section>
  );
}
