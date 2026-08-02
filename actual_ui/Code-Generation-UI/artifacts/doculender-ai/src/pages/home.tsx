import { Check, CloudUpload, Eye, FileArchive, FileCheck2, FileText, Filter, Loader2, Search, Sparkles, UploadCloud, X, Upload, AlertCircle, ArrowUpRight, ChevronRight } from 'lucide-react';
import { useGetIndexerStatus, getGetIndexerStatusQueryKey, useSearchDocuments } from '@workspace/api-client-react';
import { useQueryClient } from '@tanstack/react-query';
import JSZip from 'jszip';
import { useRequestId } from '@/lib/request-id-context';
import { useEffect, useMemo, useState } from 'react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';

type ClassifiedFile = { name: string; type: string; confidence: number; pages: number; state: 'classified' | 'review' | 'uploading' | 'uploaded', rawFile?: File; edited?: boolean };
const starterFiles: ClassifiedFile[] = [];
const demoResults: { documentType: string, score: number, blobName: string, content: string }[] = [];
const CUSTOM_CATEGORY_VALUE = '__custom_category__';
const SUGGESTED_CATEGORIES = [
  'Borrower Profile',
  'Financial Statements',
  'Bank Statements',
  'Tax Documents',
  'Collateral Documents',
  'Repayment History',
  'Covenants',
  'Compliance Documents',
  'Credit Memo',
  'Unclassified',
];

export default function Home() {
  const [files, setFiles] = useState<ClassifiedFile[]>(starterFiles);
  const { requestId, setRequestId } = useRequestId();
  const [classifying, setClassifying] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(demoResults);
  const [searchMeta, setSearchMeta] = useState<{ mode: string; vectorUsed: boolean; fallbackUsed: boolean; warning?: string } | null>(null);
  const [searchError, setSearchError] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [previewFile, setPreviewFile] = useState<File | null>(null);
  const queryClient = useQueryClient();
  const indexer = useGetIndexerStatus({ requestId }, { query: { queryKey: getGetIndexerStatusQueryKey({ requestId }), enabled: Boolean(requestId), refetchInterval: (q) => { const s = q.state.data?.indexingStatus?.toLowerCase(); return s === 'succeeded' || s === 'failed' ? false : 5000; } } });
  const search = useSearchDocuments();
  const indexed = indexer.data;
  const displayedFiles = showAll ? files : files.slice(0, 4);
  const stats = useMemo(() => ({ total: files.length, classified: files.filter((file) => file.state === 'classified' || file.state === 'uploaded').length, review: files.filter((file) => file.state === 'review').length }), [files]);
  const classificationOptions = useMemo(() => {
    const detected = files.map((file) => file.type).filter((value) => value && value !== 'Classifying...' && value !== 'Failed to classify' && value !== 'Classification error');
    return Array.from(new Set([...SUGGESTED_CATEGORIES, ...detected]));
  }, [files]);

  const handleSelectFiles = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0];
    if (!selected) return;
    setClassifying(true);
    setFiles([]); 
    setRequestId('');
    try {
      const filesToUpload: File[] = [];
      if (selected.name.toLowerCase().endsWith('.zip')) {
        const zip = new JSZip();
        const loadedZip = await zip.loadAsync(selected);
        for (const [path, zipEntry] of Object.entries(loadedZip.files)) {
          const lowerPath = path.toLowerCase();
          if (!zipEntry.dir && !lowerPath.includes('__macosx') && !lowerPath.startsWith('.')) {
            const blob = await zipEntry.async('blob');
            const type = lowerPath.endsWith('.pdf') ? 'application/pdf' : lowerPath.endsWith('.xlsx') ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : lowerPath.endsWith('.docx') ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' : 'application/octet-stream';
            filesToUpload.push(new File([blob], path.split('/').pop() || path, { type }));
          }
        }
      } else {
        filesToUpload.push(selected);
      }

      if (filesToUpload.length === 0) throw new Error('No valid files found');

      setFiles(filesToUpload.map(f => ({ name: f.name, type: 'Classifying...', confidence: 0, pages: 0, state: 'uploading' as const, rawFile: f })));

      let hasAnyResult = false;
      for (const file of filesToUpload) {
        try {
          const body = new FormData();
          body.append('file', file);
          const response = await fetch('/api/classify-file', { method: 'POST', body });
          let result: ClassifiedFile;
          if (response.ok) {
            const payload = await response.json().catch(() => null);
            result = {
              name: payload?.fileName || file.name,
              type: payload?.documentType || 'Unclassified',
              confidence: payload?.confidence ? Math.round(payload.confidence * 100) : 0,
              pages: 1,
              state: payload?.status === 'Failed' ? 'review' : 'classified',
              rawFile: file
            };
          } else {
            result = { name: file.name, type: 'Failed to classify', confidence: 0, pages: 0, state: 'review', rawFile: file };
          }
          hasAnyResult = true;
          setFiles(current => current.map(f => f.name === file.name ? result : f));
        } catch (e) {
          console.error(`Classification failed for ${file.name}`, e);
          hasAnyResult = true;
          setFiles(current => current.map(f => f.name === file.name ? { ...f, name: file.name, type: 'Classification error', confidence: 0, pages: 0, state: 'review' as const, rawFile: file } : f));
        }
      }

      if (!hasAnyResult && filesToUpload.length > 0) {
        setFiles([{ name: selected.name, type: 'Classification failed', confidence: 0, pages: 0, state: 'review' }]);
      }

    } catch (e) {
      console.error(e);
      setFiles([{ name: selected.name, type: 'Classification failed', confidence: 0, pages: 0, state: 'review' }]);
    } finally {
      setClassifying(false);
    }
  };

  const handleUploadClassified = async () => {
    const filesToUpload = files.filter(f => f.rawFile && (f.state === 'classified' || f.state === 'review'));
    if (!filesToUpload.length) return;
    
    setUploading(true);
    const nextRequest = `IDX-${new Date().toISOString().slice(2, 10).replaceAll('-', '')}-${Math.random().toString(16).slice(2, 6).toUpperCase()}`;
    setRequestId(nextRequest);
    
    try {
      for (const fileObj of filesToUpload) {
        const body = new FormData();
        body.append('file', fileObj.rawFile!);
        body.append('fileName', fileObj.name);
        body.append('requestId', nextRequest);
        body.append('documentType', fileObj.type);
        body.append('confidence', (fileObj.confidence / 100).toString());
        body.append('status', fileObj.state === 'review' ? 'Failed' : 'Success');
        
        setFiles(current => current.map(f => f.name === fileObj.name ? { ...f, state: 'uploading' } : f));
        
        await fetch('/api/upload', { method: 'POST', body });
        
        setFiles(current => current.map(f => f.name === fileObj.name ? { ...f, state: 'uploaded' } : f));
      }
      
      await fetch('/api/trigger-indexing', { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requestId: nextRequest }) 
      });
    } catch (e) {
      console.error('Upload failed', e);
    } finally {
      setUploading(false);
      queryClient.invalidateQueries({ queryKey: getGetIndexerStatusQueryKey({ requestId: nextRequest }) });
    }
  };

  const runSearch = () => {
    if (!query.trim()) return;
    setSearchError('');
    setSearchMeta(null);
    search.mutate({ data: { query, requestId, top: 10 } }, {
      onSuccess: (data: any) => {
        setResults(data?.results?.length ? data.results : []);
        setSearchMeta({
          mode: data?.searchMode || 'keyword',
          vectorUsed: Boolean(data?.vectorUsed),
          fallbackUsed: Boolean(data?.fallbackUsed),
          warning: data?.warning,
        });
      },
      onError: () => setSearchError('Search service unavailable. Ensure backend is running.'),
    });
  };

  const status = (indexed?.indexingStatus ?? (requestId ? (uploading ? 'running' : 'pending') : 'idle')).toLowerCase();
  const indexStatusMessage = indexed?.indexingErrors?.length
    ? indexed.indexingErrors.join('; ')
    : status === 'running' || status === 'pending'
      ? 'Indexing documents...'
      : status === 'succeeded'
        ? 'All package assets are searchable for memo narratives.'
        : 'Indexing failed.';
  const canUpload = files.some(f => f.rawFile && (f.state === 'classified' || f.state === 'review') && f.type.trim() && f.type !== 'Classifying...' && f.type !== 'Classification error') && !uploading && !classifying;

  return (
    <div className="mx-auto max-w-[1440px] px-5 pb-16 pt-8 md:px-8 lg:px-10">
      <div className="mb-8 flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
        <div>
          <div className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground"><span className="h-1.5 w-1.5 rounded-full bg-[#4f967d]" /> Narrative intake console <span className="mx-1.5 inline-block h-0.5 w-0.5 rounded-full bg-border align-middle" /> {stats.total} indexed assets</div>
          <h1 className="font-serif text-[clamp(2.5rem,5vw,4.3rem)] leading-[0.92] tracking-[-0.04em] text-foreground">Document intelligence,<br /><em className="text-[#a17734]">without the drag.</em></h1>
          <p className="mt-4 max-w-xl text-sm leading-6 text-muted-foreground">Classify a lending package, verify the signal, and make every source traceable before it reaches a credit memo narrative.</p>
        </div>
        <div className="grid grid-cols-3 divide-x divide-border border-y border-border py-3 xl:min-w-[430px]">
          <Metric label="Documents" value={String(stats.total)} caption="in corpus" />
          <Metric label="Classified" value={String(stats.classified)} caption="high confidence" />
          <Metric label="Review queue" value={String(stats.review).padStart(2, '0')} caption="needs eyes" accent />
        </div>
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.16fr)_minmax(390px,.84fr)]">
        <section className="overflow-hidden rounded-lg border border-border bg-card shadow-[0_10px_30px_-24px_rgba(40,35,20,.5)]">
          <div className="flex items-center justify-between border-b border-border px-5 py-4 md:px-6">
            <div><div className="flex items-center gap-2 text-sm font-semibold"><FileArchive size={16} className="text-[#a17734]" /> Package intake</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">ZIP / PDF / XLSX<span className="mx-1.5 inline-block h-0.5 w-0.5 rounded-full bg-border align-middle" />max 250 MB</div></div>
            <label className={`group flex cursor-pointer items-center gap-2 rounded-md bg-primary px-3.5 py-2.5 text-xs font-semibold text-primary-foreground transition-transform hover:-translate-y-0.5 ${classifying || uploading ? 'pointer-events-none opacity-70' : ''}`} data-testid="button-upload-package"><UploadCloud size={15} /> {classifying ? 'Classifying…' : 'Select package'}<input className="sr-only" type="file" accept=".zip,.pdf,.xlsx" onChange={handleSelectFiles} data-testid="input-upload-package" /></label>
          </div>
          <div className="grid gap-3 border-b border-border bg-[#f4f0e7] px-5 py-4 md:grid-cols-[1fr_auto] md:items-center md:px-6">
            <div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-md border border-[#dfd4bd] bg-card"><FileArchive size={20} className="text-[#a17734]" /></div><div><div className="text-sm font-semibold">{classifying ? 'Classifying new package…' : (requestId ? 'Uploaded Package' : files.length ? 'Classified Package' : 'No package selected')}</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">{requestId || 'Pending upload'}</div></div></div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {canUpload ? (
                 <button onClick={handleUploadClassified} className="flex items-center gap-1.5 rounded-md bg-[#39745e] px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[#274f41]"><Upload size={14} /> Commit & Upload</button>
              ) : requestId ? (
                 <><span className={`h-2 w-2 rounded-full ${status === 'failed' ? 'bg-destructive' : status === 'succeeded' ? 'bg-[#4f967d]' : 'animate-pulse bg-[#d29439]'}`} /> {status === 'succeeded' ? 'Indexed' : status === 'failed' ? 'Index failed' : 'Indexing'}</>
              ) : null}
            </div>
          </div>
          <div className="px-5 py-3 md:px-6">
            <div className="mb-3 flex items-center justify-between"><div className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground">Classified documents <span className="ml-1 text-foreground">{files.length}</span></div><button type="button" data-testid="button-filter-documents" className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"><Filter size={13} /> Filter</button></div>
            <div className="divide-y divide-border">
              {displayedFiles.map((file, index) => <FileRow key={`${file.name}-${index}`} file={file} categories={classificationOptions} onPreview={file.rawFile ? () => setPreviewFile(file.rawFile!) : undefined} onCategoryChange={(nextType) => setFiles((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, type: nextType, edited: true } : item))} />)}
            </div>
            {files.length > 4 && <button type="button" data-testid="button-view-all-documents" onClick={() => setShowAll((value) => !value)} className="mt-4 flex w-full items-center justify-center gap-1 py-2 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground transition-colors hover:text-foreground">{showAll ? 'Show less' : `View all ${stats.total} documents`} <ChevronRight size={13} /></button>}
          </div>
        </section>
        <div className="space-y-5">
          {requestId ? (
             <section className="rounded-lg border border-border bg-[#e7eee8] p-5 md:p-6">
               <div className="flex items-start justify-between"><div><div className="flex items-center gap-2 text-sm font-semibold"><CloudUpload size={16} className="text-[#39745e]" /> Azure indexing</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-[#39745e]/70">Live request telemetry</div></div><span className={`rounded-full px-2 py-1 font-mono text-[9px] uppercase tracking-[0.1em] ${status === 'failed' ? 'bg-red-100 text-red-700' : status === 'succeeded' ? 'bg-[#d0e2d5] text-[#39745e]' : 'bg-amber-100 text-amber-700'}`}>{status}</span></div>
                <div className="mt-6 flex items-end justify-between"><div className="space-y-1"><div className="text-xs leading-5 text-[#39745e]/75">{indexStatusMessage}</div><div className="font-mono text-[9px] uppercase tracking-[0.1em] text-[#39745e]/50">Request {requestId}</div></div></div>
               <div className="mt-4 h-2 overflow-hidden rounded-full bg-[#cadbcf]">
                 {status === 'running' || status === 'pending' ? (
                   <div className="h-full w-full origin-left animate-pulse rounded-full bg-gradient-to-r from-[#39745e]/40 via-[#39745e] to-[#39745e]/40" style={{ animation: 'shimmer 2s ease-in-out infinite' }} />
                 ) : status === 'succeeded' ? (
                   <div className="h-full w-full rounded-full bg-[#39745e]" />
                 ) : (
                   <div className="h-full w-full rounded-full bg-red-400" />
                 )}
               </div>
               <style>{`@keyframes shimmer { 0%,100% { opacity: .4; transform: scaleX(.6); } 50% { opacity: 1; transform: scaleX(1); } }`}</style>
               <div className="mt-4 flex items-center justify-between border-t border-[#c8dbcd] pt-3 font-mono text-[9px] uppercase tracking-[0.1em] text-[#39745e]/65"><span>Blob storage</span><span className="flex items-center gap-1"><Check size={12} /> Connected</span><span>Vector index</span><span className="flex items-center gap-1"><Check size={12} /> Ready</span></div>
             </section>
          ) : (
             <section className="flex min-h-[220px] flex-col items-center justify-center rounded-lg border border-dashed border-border p-6 text-center text-muted-foreground">
                <CloudUpload size={24} className="mb-3 text-[#39745e]/50" />
                 <div className="text-sm font-semibold text-foreground">Azure Narrative Indexing Standby</div>
                 <div className="mt-2 text-xs">Waiting for package classification and upload confirmation before narrative drafting.</div>
             </section>
          )}
          <section className="rounded-lg border border-border bg-card p-5 md:p-6">
             <div className="flex items-start justify-between"><div><div className="flex items-center gap-2 text-sm font-semibold"><Search size={16} className="text-[#a17734]" /> Search narrative evidence</div><div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">{searchMeta ? `${searchMeta.mode}${searchMeta.vectorUsed ? ' (vector on)' : ' (vector off)'}` : 'Hybrid semantic + keyword'}</div></div><Sparkles size={16} className="text-[#d29439]" /></div>
             <div className="mt-5 flex gap-2"><div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-3 text-muted-foreground" size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && runSearch()} placeholder="Try “DSCR covenant narrative”" className="h-10 w-full rounded-md border border-input bg-background pl-9 pr-3 text-xs outline-none transition-colors placeholder:text-muted-foreground/70 focus:border-[#a17734] focus:ring-2 focus:ring-[#a17734]/15" data-testid="input-search-corpus" /></div><button type="button" onClick={runSearch} disabled={search.isPending || !query.trim()} data-testid="button-search-corpus" className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-primary text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40">{search.isPending ? <Loader2 className="animate-spin" size={15} /> : <ArrowUpRight size={16} />}</button></div>
            {searchError && <div className="mt-3 flex gap-2 text-[11px] text-[#9a692d]"><AlertCircle size={14} className="shrink-0" />{searchError}</div>}
            {searchMeta?.warning && <div className="mt-2 flex gap-2 text-[11px] text-[#9a692d]"><AlertCircle size={14} className="shrink-0" />{searchMeta.warning}</div>}
             <div className="mt-5 space-y-3">{results.length ? results.slice(0, 3).map((result, index) => <SearchResult key={`${result.blobName}-${index}`} result={result} />) : <div className="rounded-md border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">No matching narrative excerpts in this index.</div>}</div>
          </section>
        </div>
      </div>

      <FilePreviewDialog
        file={previewFile}
        onClose={() => setPreviewFile(null)}
      />
    </div>
  );
}

function Metric({ label, value, caption, accent = false }: { label: string; value: string; caption: string; accent?: boolean }) {
  return <div className="px-4 first:pl-0 last:pr-0"><div className={`font-serif text-3xl ${accent ? 'text-[#b07831]' : 'text-foreground'}`}>{value}</div><div className="mt-1 text-[11px] font-semibold">{label}</div><div className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">{caption}</div></div>;
}

function confidenceScoreColor(confidence: number): string {
  if (confidence >= 80) return 'text-[#39745e]';
  if (confidence >= 50) return 'text-[#b07831]';
  return 'text-destructive';
}

function FileRow({ file, categories, onPreview, onCategoryChange }: { file: ClassifiedFile; categories: string[]; onPreview?: () => void; onCategoryChange?: (value: string) => void }) {
  const stateLabel = file.state === 'uploading' ? 'Uploading' : file.state === 'review' ? 'Review' : `${file.confidence}%`;
  const stateColor = file.state === 'review' ? 'text-[#aa702d]' : file.state === 'uploading' ? 'text-muted-foreground' : confidenceScoreColor(file.confidence);
  const caption = file.state === 'review' ? 'flagged' : file.state === 'uploading' ? 'processing' : file.state === 'uploaded' ? 'uploaded' : 'confidence';
  const icon = file.state === 'review' ? <AlertCircle size={15} className="text-[#b07831]" /> : file.state === 'uploading' ? <Loader2 size={15} className="animate-spin text-muted-foreground" /> : <FileCheck2 size={15} className="text-[#4f967d]" />;
  const [customMode, setCustomMode] = useState(false);
  const knownCategory = categories.includes(file.type);
  const selectedCategory = customMode ? CUSTOM_CATEGORY_VALUE : knownCategory ? file.type : CUSTOM_CATEGORY_VALUE;

  useEffect(() => {
    if (file.type === '') setCustomMode(true);
  }, [file.type]);

  return (
    <div className="flex items-start gap-3 py-3" data-testid={`row-document-${file.name}`}>
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded bg-muted">
        <FileText size={15} className="text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-semibold">{file.name}</div>
        <div className="mt-1 flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.06em] text-muted-foreground">
          <span>{file.pages || '—'} pages</span>
          <span className="mx-1.5 inline-block h-0.5 w-0.5 rounded-full bg-border align-middle" />
          <span>{file.state === 'review' ? 'Needs review' : 'Classified'}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="rounded border border-[#d6c39f] bg-[#efe2c7] px-2 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-[#7a5b26]">{file.type}</span>
          {file.edited ? <span className="rounded border border-[#aac9bb] bg-[#e4f0e9] px-2 py-1 font-mono text-[9px] uppercase tracking-[0.08em] text-[#39745e]">Edited</span> : null}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="font-mono text-[9px] uppercase tracking-[0.08em] text-muted-foreground">Category</label>
          <select
            value={selectedCategory}
            onChange={(event) => {
              const value = event.target.value;
              if (value === CUSTOM_CATEGORY_VALUE) {
                setCustomMode(true);
                if (knownCategory) onCategoryChange?.('');
                return;
              }
              setCustomMode(false);
              onCategoryChange?.(value);
            }}
            className="h-7 rounded border border-input bg-background px-2 text-[11px]"
          >
            {categories.map((category) => <option key={category} value={category}>{category}</option>)}
            <option value={CUSTOM_CATEGORY_VALUE}>Custom...</option>
          </select>
          {customMode ? (
            <input
              value={file.type}
              onChange={(event) => onCategoryChange?.(event.target.value)}
              placeholder="Type custom category"
              className="h-7 min-w-[180px] rounded border border-input bg-background px-2 text-[11px]"
            />
          ) : null}
        </div>
      </div>
      <div className="hidden items-center gap-2 sm:flex">
        <button type="button" onClick={onPreview} disabled={!onPreview} className="rounded p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-30">
          <Eye size={14} />
        </button>
        <div className="text-right">
          <div className={`font-mono text-[11px] ${stateColor}`}>{stateLabel}</div>
          <div className="mt-1 font-mono text-[9px] uppercase tracking-[0.08em] text-muted-foreground">{caption}</div>
        </div>
      </div>
      {icon}
    </div>
  );
}

function SearchResult({ result }: { result: typeof demoResults[number] }) {
  return <div className="group rounded-md border border-border/80 p-3 transition-colors hover:border-[#bea76f] hover:bg-[#fbf8f1]" data-testid={`result-search-${result.blobName}`}><div className="flex items-center justify-between gap-3"><span className="truncate font-mono text-[9px] uppercase tracking-[0.1em] text-[#a17734]">{result.documentType}</span><span className="shrink-0 font-mono text-[10px] text-muted-foreground">score {(result.score * 100).toFixed(1)}%</span></div><div className="mt-2 line-clamp-2 text-xs leading-5 text-foreground/75">{result.content}</div><div className="mt-2 flex items-center gap-1 font-mono text-[9px] text-muted-foreground"><FileText size={11} /> {result.blobName}</div></div>;
}

const PREVIEWABLE_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.txt', '.csv'];

function isPreviewableFile(file: File): boolean {
  if (PREVIEWABLE_EXTENSIONS.some(ext => file.name.toLowerCase().endsWith(ext))) return true;
  return ['application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'text/plain', 'text/csv'].includes(file.type);
}

function isImageFile(file: File): boolean {
  return file.type.startsWith('image/') || /\.(png|jpg|jpeg|gif|webp)$/i.test(file.name);
}

function isPdfFile(file: File): boolean {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
}

function FilePreviewDialog({ file, onClose }: { file: File | null; onClose: () => void }) {
  const open = file !== null;
  const previewable = file ? isPreviewableFile(file) : false;
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  const handleClose = () => {
    if (objectUrl) { URL.revokeObjectURL(objectUrl); setObjectUrl(null); }
    onClose();
  };

  const handleOpenChange = (open: boolean) => { if (!open) handleClose(); };

  useEffect(() => {
    if (!file) { setObjectUrl(null); return; }
    const url = URL.createObjectURL(file);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] flex flex-col gap-0 p-0">
        <DialogTitle className="sr-only">Preview: {file?.name}</DialogTitle>
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <FileText size={15} className="shrink-0 text-muted-foreground" />
            <span className="truncate text-sm font-semibold">{file?.name}</span>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">({file ? (file.size / 1024).toFixed(0) : '—'} KB)</span>
          </div>
        </div>
        <div className="flex-1 overflow-auto p-5">
          {previewable && objectUrl && file ? (
            isPdfFile(file) ? (
              <iframe src={objectUrl} className="h-[65vh] w-full rounded border" title={file.name} />
            ) : isImageFile(file) ? (
              <img src={objectUrl} alt={file.name} className="mx-auto max-h-[65vh] rounded object-contain" />
            ) : (
              <object data={objectUrl} type={file.type} className="h-[65vh] w-full rounded border">
                <div className="flex h-full items-center justify-center text-xs text-muted-foreground">Preview not available in browser.</div>
              </object>
            )
          ) : (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
              <FileText size={32} className="text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">Inline preview not available for <span className="font-semibold">{file?.name || 'this file type'}</span>.</p>
              <p className="text-xs text-muted-foreground/60">You can download the file to view it locally.</p>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
