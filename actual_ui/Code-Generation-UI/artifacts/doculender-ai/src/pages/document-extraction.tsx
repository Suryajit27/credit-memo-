import { CheckCircle2, FileJson2, FileText, Image, Loader2, Play, Plus, Save, Sparkles, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useRequestId } from '@/lib/request-id-context';

type Extraction = {
  status?: 'finalized';
  schemaVersion?: string;
  fields: Record<string, CanonicalField>;
  findings?: string[];
  validation?: ValidationResult;
  finalizedAt?: string;
  updatedAt?: string;
};

type SourceEvidence = { provider: string; model: string; page: number; evidence: string; confidence: number };
type Candidate = { id: string; value: unknown; valueType: string; confidence: number; source: SourceEvidence };
type CanonicalField = { value: unknown; valueType: string; confidence: number; status: string; sources: SourceEvidence[]; candidates: Candidate[] };
type RepairPayload = { stage: 'layout' | 'vision'; rawOutput: string; baseFields: Record<string, unknown> };
type ValidationCheck = { fieldName: string; status: string; expectedValue?: unknown; extractedValue?: unknown; comparison?: string; similarityPercent?: number; page?: number; evidence?: string; reason?: string };
type ValidationResult = { status: string; sourceGroup?: string; checks: ValidationCheck[] };

type RequestDocument = {
  originalPath: string;
  blobName: string;
  documentType: string;
  confidence?: number;
  status: string;
  uploadedAt?: string;
  extraction?: Extraction;
};

function isPdf(document: RequestDocument) {
  return document.originalPath.toLowerCase().endsWith('.pdf');
}

function isImage(document: RequestDocument) {
  return /\.(png|jpe?g|gif|webp)$/i.test(document.originalPath);
}

export default function DocumentExtraction() {
  const { requestId } = useRequestId();
  const [documents, setDocuments] = useState<RequestDocument[]>([]);
  const [selectedBlobName, setSelectedBlobName] = useState('');
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [repair, setRepair] = useState<RepairPayload | null>(null);
  const [repairText, setRepairText] = useState('');
  const [repairing, setRepairing] = useState(false);

  const selected = documents.find((document) => document.blobName === selectedBlobName) ?? null;

  useEffect(() => {
    if (!requestId) {
      setDocuments([]);
      setSelectedBlobName('');
      return;
    }
    let active = true;
    setLoading(true);
    setError('');
    fetch(`/api/document-extraction/documents?requestId=${encodeURIComponent(requestId)}`)
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Could not load request documents.');
        return payload.documents as RequestDocument[];
      })
      .then((nextDocuments) => {
        if (!active) return;
        setDocuments(nextDocuments);
        const nextSelected = nextDocuments.find((document) => document.blobName === selectedBlobName) || nextDocuments[0];
        setSelectedBlobName(nextSelected?.blobName || '');
      })
      .catch((reason) => active && setError(reason.message))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [requestId]);

  useEffect(() => {
    if (!selected) {
      setExtraction(null);
      return;
    }
    setExtraction(selected.extraction ?? null);
    setRepair(null);
    setError('');
    setNotice(selected.extraction?.finalizedAt ? `Finalized ${new Date(selected.extraction.finalizedAt).toLocaleString()}` : '');
  }, [selectedBlobName, documents]);

  const extract = async () => {
    if (!selected || !requestId) return;
    setExtracting(true);
    setError('');
    setNotice('Reading layout and extracting document fields…');
    try {
      const response = await fetch('/api/document-extraction/extract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requestId, blobName: selected.blobName }),
      });
      const responseText = await response.text();
      let payload: any;
      try {
        payload = responseText ? JSON.parse(responseText) : {};
      } catch {
        throw new Error(responseText || `Extraction failed with status ${response.status}.`);
      }
      if (response.status === 422 && payload.repair) {
        setRepair(payload.repair);
        setRepairText(payload.repair.rawOutput);
        setNotice('The agent response needs a JSON repair. Correct it below without rerunning extraction.');
        return;
      }
      if (!response.ok) throw new Error(payload.error || 'Extraction failed.');
      setExtraction(payload.extraction);
      setRepair(null);
      setNotice('Base and vision candidates are ready for evidence review.');
    } catch (reason: any) {
      setNotice('');
      setError(reason.message || 'Extraction failed.');
    } finally {
      setExtracting(false);
    }
  };

  const applyRepair = async () => {
    if (!selected || !requestId || !repair) return;
    setRepairing(true);
    setError('');
    try {
      const response = await fetch('/api/document-extraction/repair', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requestId, blobName: selected.blobName, stage: repair.stage, rawOutput: repairText, baseFields: repair.baseFields }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not apply the corrected JSON.');
      setExtraction(payload.extraction);
      setRepair(null);
      setNotice('Corrected JSON applied. Review the evidence candidates and finalize when ready.');
    } catch (reason: any) {
      setError(reason.message || 'Could not apply the corrected JSON.');
    } finally {
      setRepairing(false);
    }
  };

  const finalize = async () => {
    if (!selected || !requestId || !extraction) {
      setError('Run extraction before finalizing.');
      return;
    }
    setFinalizing(true);
    setError('');
    try {
      const response = await fetch('/api/document-extraction/finalize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requestId, blobName: selected.blobName, extraction }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not finalize extraction.');
      setDocuments((current) => current.map((document) => document.blobName === selected.blobName ? { ...document, extraction: payload.extraction } : document));
      setNotice(`Finalized ${new Date(payload.extraction.finalizedAt).toLocaleString()}`);
    } catch (reason: any) {
      setError(reason.message || 'Could not finalize extraction.');
    } finally {
      setFinalizing(false);
    }
  };

  const previewUrl = selected && requestId
    ? `/api/document-extraction/preview?requestId=${encodeURIComponent(requestId)}&blobName=${encodeURIComponent(selected.blobName)}`
    : '';

  return (
    <div className="mx-auto max-w-[1600px] px-5 pb-10 pt-8 md:px-8 lg:px-10">
      <div className="mb-7 flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
        <div>
          <div className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground"><span className="h-1.5 w-1.5 rounded-full bg-[#4f967d]" /> Request-scoped review</div>
          <h1 className="font-serif text-[clamp(2.4rem,4.5vw,4rem)] leading-[0.95] tracking-[-0.04em]">Document field<br /><em className="text-[#a17734]">extraction desk.</em></h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">Choose a document, extract its visible fields, correct the JSON, and finalize the reviewed result to the request record.</p>
        </div>
        <div className="border-l-2 border-[#a17734] bg-[#f4f0e7] px-4 py-3"><div className="font-mono text-[9px] uppercase tracking-[0.13em] text-muted-foreground">Active request</div><div className="mt-1 font-mono text-xs font-semibold text-foreground">{requestId || 'Upload a package to begin'}</div></div>
      </div>

      {!requestId ? <EmptyState message="Upload and commit a document package first. Its request ID will appear here automatically." /> : (
        <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="rounded-lg border border-border bg-card shadow-[0_10px_30px_-24px_rgba(40,35,20,.5)]">
            <div className="border-b border-border px-4 py-4"><div className="flex items-center gap-2 text-sm font-semibold"><FileText size={16} className="text-[#a17734]" /> Source documents</div><div className="mt-1 font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">{loading ? 'Loading…' : `${documents.length} available`}</div></div>
            <div className="max-h-[calc(100vh-280px)] overflow-y-auto p-2">
              {documents.map((document) => <button key={document.blobName} type="button" onClick={() => setSelectedBlobName(document.blobName)} className={`mb-1 w-full rounded-md p-3 text-left transition-colors ${selectedBlobName === document.blobName ? 'bg-[#eee4d2] ring-1 ring-[#c8a86d]' : 'hover:bg-muted'}`}>
                <div className="flex gap-2"><FileText size={15} className="mt-0.5 shrink-0 text-muted-foreground" /><div className="min-w-0"><div className="truncate text-xs font-semibold">{document.originalPath}</div><div className="mt-1 truncate font-mono text-[9px] uppercase tracking-[0.08em] text-muted-foreground">{document.documentType || 'Unclassified'}</div>{document.extraction ? <div className="mt-2 flex items-center gap-1 text-[10px] font-semibold text-[#39745e]"><CheckCircle2 size={12} /> Finalized</div> : <div className="mt-2 text-[10px] text-[#a17734]">Not extracted</div>}</div></div>
              </button>)}
              {!loading && !documents.length ? <div className="p-4 text-center text-xs text-muted-foreground">No documents were found for this request.</div> : null}
            </div>
          </aside>

          <section className="min-w-0 rounded-lg border border-border bg-card shadow-[0_10px_30px_-24px_rgba(40,35,20,.5)]">
            {selected ? <>
              <div className="flex flex-col gap-3 border-b border-border px-5 py-4 md:flex-row md:items-center md:justify-between"><div className="min-w-0"><div className="truncate text-sm font-semibold">{selected.originalPath}</div><div className="mt-1 font-mono text-[9px] uppercase tracking-[0.11em] text-muted-foreground">{selected.documentType || 'Unclassified'} <span className="mx-1">•</span> {selected.status}</div></div><div className="flex shrink-0 gap-2"><button type="button" onClick={extract} disabled={extracting} className="flex h-9 items-center gap-2 rounded-md border border-[#d6c39f] bg-[#f8f1e3] px-3 text-xs font-semibold text-[#72501d] hover:bg-[#efe2c7] disabled:opacity-50">{extracting ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} {extracting ? 'Extracting…' : 'Extract fields'}</button><button type="button" onClick={finalize} disabled={finalizing || extracting} className="flex h-9 items-center gap-2 rounded-md bg-[#39745e] px-3 text-xs font-semibold text-white hover:bg-[#274f41] disabled:opacity-50">{finalizing ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Finalize</button></div></div>
              {(error || notice) && <div className={`mx-5 mt-4 rounded-md px-3 py-2 text-xs ${error ? 'bg-red-50 text-red-700' : 'bg-[#e7eee8] text-[#39745e]'}`}>{error || notice}</div>}
              <div className="grid min-h-[680px] gap-0 xl:grid-cols-2">
                <div className="border-b border-border p-4 xl:border-b-0 xl:border-r"><div className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground">{isPdf(selected) ? <FileText size={13} /> : <Image size={13} />} Source preview</div><div className="h-[610px] overflow-hidden rounded-md border border-border bg-[#f5f5f5]">{isPdf(selected) ? <iframe title={`Preview ${selected.originalPath}`} src={previewUrl} className="h-full w-full" /> : isImage(selected) ? <img src={previewUrl} alt={selected.originalPath} className="h-full w-full object-contain" /> : <div className="flex h-full flex-col items-center justify-center p-8 text-center text-sm text-muted-foreground"><FileText size={30} className="mb-3 text-muted-foreground/50" />Preview is available for PDF and image documents in this release.</div>}</div></div>
                <div className="min-h-0 p-4"><div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.13em] text-muted-foreground"><FileJson2 size={13} /> Evidence review</div><span className="text-[10px] text-muted-foreground">Select a candidate or edit the reviewed value</span></div><div className="h-[610px] overflow-y-auto rounded-md border border-border bg-[#fbfaf7] p-3">{repair ? <JsonRepairPanel repair={repair} value={repairText} onChange={setRepairText} onApply={applyRepair} applying={repairing} /> : extraction ? <><ValidationPanel validation={extraction.validation} /><EvidenceFieldTable fields={extraction.fields} onChange={(fields) => setExtraction({ ...extraction, fields, validation: undefined })} />{extraction.findings?.length ? <div className="mt-4 rounded-md border border-[#e5d5ad] bg-[#fff8e7] p-3"><div className="font-mono text-[9px] uppercase tracking-[0.1em] text-[#72501d]">Agent findings</div><div className="mt-2 space-y-1 text-xs text-[#72501d]">{extraction.findings.map((finding, index) => <div key={index}>{finding}</div>)}</div></div> : null}</> : <div className="flex h-full items-center justify-center text-center text-sm text-muted-foreground">Run extraction to compare Document Intelligence and vision-agent evidence.</div>}</div></div>
              </div>
            </> : <EmptyState message="Choose a source document to begin extraction." />}
          </section>
        </div>
      )}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <div className="flex min-h-[360px] flex-col items-center justify-center rounded-lg border border-dashed border-border bg-card p-8 text-center"><Play size={26} className="mb-4 text-[#a17734]/60" /><div className="max-w-md text-sm leading-6 text-muted-foreground">{message}</div></div>;
}

function JsonRepairPanel({ repair, value, onChange, onApply, applying }: { repair: RepairPayload; value: string; onChange: (value: string) => void; onApply: () => void; applying: boolean }) {
  return <div className="flex h-full flex-col"><div className="rounded-md border border-[#e5d5ad] bg-[#fff8e7] p-3"><div className="text-xs font-semibold text-[#72501d]">Repair {repair.stage} response</div><p className="mt-1 text-xs leading-5 text-[#72501d]">Correct the JSON returned by the completed agent stage. Applying this does not call the extraction agent again.</p></div><textarea value={value} onChange={(event) => onChange(event.target.value)} spellCheck={false} aria-label="Corrected extraction JSON" className="mt-3 min-h-0 flex-1 resize-none rounded-md border border-border bg-[#151a20] p-3 font-mono text-xs leading-5 text-[#e8edf1] outline-none focus:border-[#a17734] focus:ring-2 focus:ring-[#a17734]/15" /><div className="mt-3 flex justify-end"><button type="button" onClick={onApply} disabled={applying} className="flex items-center gap-2 rounded-md bg-[#39745e] px-3 py-2 text-xs font-semibold text-white hover:bg-[#274f41] disabled:opacity-50">{applying ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />} Apply corrected JSON</button></div></div>;
}

function ValidationPanel({ validation }: { validation?: ValidationResult }) {
  if (!validation) return null;
  const tone = validation.status === 'passed' ? 'border-[#9abda5] bg-[#edf6ef] text-[#276044]' : validation.status === 'missing_source_data' || validation.status === 'not_configured' ? 'border-[#e5d5ad] bg-[#fff8e7] text-[#72501d]' : 'border-[#e5b6b1] bg-[#fff1f0] text-[#9d3028]';
  return <section className={`mb-4 overflow-hidden rounded-md border ${tone}`}>
    <div className="flex items-center justify-between gap-3 px-3 py-2.5"><div><div className="font-mono text-[9px] uppercase tracking-[0.1em]">Cosmos validation</div><div className="mt-1 text-xs font-semibold">{validation.status.replaceAll('_', ' ')}</div></div><div className="text-right font-mono text-[9px] uppercase tracking-[0.08em]">{validation.sourceGroup || 'No source group'}</div></div>
    {validation.checks.length ? <div className="divide-y divide-current/15 border-t border-current/15 bg-white/45">{validation.checks.map((check) => <div key={check.fieldName} className="p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div className="text-xs font-semibold">{check.fieldName}</div><div className="flex items-center gap-2">{check.similarityPercent != null ? <span className="font-mono text-[10px] text-foreground/70" title="Normalized value similarity">{check.similarityPercent}% match</span> : null}<span className={`rounded px-2 py-1 font-mono text-[9px] uppercase tracking-[0.08em] ${check.status === 'passed' ? 'bg-[#dcece1] text-[#276044]' : check.status === 'review_required' ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-700'}`}>{check.status.replaceAll('_', ' ')}</span></div></div>{check.status !== 'passed' ? <><div className="mt-2 grid gap-2 text-xs sm:grid-cols-2"><ValidationValue label="Cosmos value" value={check.expectedValue} /><ValidationValue label="Extracted value" value={check.extractedValue} /></div>{check.reason ? <div className="mt-2 text-xs leading-5">{check.reason}</div> : null}{check.evidence ? <div className="mt-2 text-xs leading-5 text-foreground/70">Page {check.page}: “{check.evidence}”</div> : null}</> : null}</div>)}</div> : <div className="border-t border-current/15 px-3 py-2.5 text-xs">No configured Cosmos source data is available for this document type.</div>}
  </section>;
}

function ValidationValue({ label, value }: { label: string; value: unknown }) {
  return <div className="rounded border border-current/15 bg-white/60 p-2"><div className="font-mono text-[9px] uppercase tracking-[0.08em] text-muted-foreground">{label}</div><div className="mt-1 break-words text-xs text-foreground"><StructuredValue value={value} /></div></div>;
}

function readableKey(value: string) {
  return value.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ');
}

function StructuredValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null) return <>Not available</>;
  if (Array.isArray(value)) return <div className="space-y-2">{value.map((item, index) => <div key={index} className="rounded border border-border/70 bg-background/70 p-2"><div className="mb-1 font-mono text-[9px] uppercase tracking-[0.08em] text-muted-foreground">Item {index + 1}</div><StructuredValue value={item} depth={depth + 1} /></div>)}</div>;
  if (typeof value === 'object') return <dl className="grid gap-x-3 gap-y-1.5 sm:grid-cols-[minmax(110px,.42fr)_minmax(0,1fr)]">{Object.entries(value as Record<string, unknown>).map(([key, item]) => <div key={key} className="contents"><dt className="font-mono text-[9px] uppercase tracking-[0.06em] text-muted-foreground">{readableKey(key)}</dt><dd className="min-w-0 break-words"><StructuredValue value={item} depth={depth + 1} /></dd></div>)}</dl>;
  return <>{String(value)}</>;
}

function EvidenceFieldTable({ fields, onChange }: { fields: Record<string, CanonicalField>; onChange: (fields: Record<string, CanonicalField>) => void }) {
  const update = (name: string, field: CanonicalField) => onChange({ ...fields, [name]: field });
  const selectCandidate = (name: string, field: CanonicalField, candidate: Candidate) => update(name, { ...field, value: candidate.value, valueType: candidate.valueType, confidence: candidate.confidence, status: 'reviewed' });

  return <div className="space-y-3">{Object.entries(fields).map(([name, field]) => <section key={name} className="overflow-hidden rounded-md border border-[#dfd8ca] bg-white">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#e6e0d5] bg-[#f7f4ed] px-3 py-2"><div className="text-xs font-semibold">{name}</div><span className={`rounded px-2 py-1 font-mono text-[9px] uppercase tracking-[0.09em] ${field.status === 'conflict' ? 'bg-amber-100 text-amber-800' : field.status === 'reviewed' ? 'bg-[#dcece1] text-[#276044]' : 'bg-[#e8edf1] text-[#53606c]'}`}>{field.status}</span></div>
    <div className="p-3"><div className="mb-3"><div className="mb-1 font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">Reviewed value</div><ValueEditor value={field.value} onChange={(value) => update(name, { ...field, value, status: 'edited' })} /></div>
      <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">Evidence candidates</div><div className="mt-2 grid gap-2">{field.candidates.map((candidate) => <button key={candidate.id} type="button" onClick={() => selectCandidate(name, field, candidate)} className={`rounded border p-2.5 text-left transition-colors ${JSON.stringify(field.value) === JSON.stringify(candidate.value) ? 'border-[#6b9b7d] bg-[#edf6ef]' : 'border-border hover:border-[#c8a86d] hover:bg-[#fffaf0]'}`}><div className="flex items-start justify-between gap-3"><div className="min-w-0 flex-1 text-xs font-semibold break-words"><StructuredValue value={candidate.value} /></div><span className="shrink-0 font-mono text-[10px] text-muted-foreground">{Math.round(candidate.confidence * 100)}%</span></div><div className="mt-1.5 font-mono text-[9px] uppercase tracking-[0.08em] text-muted-foreground">{candidate.source.provider.replaceAll('_', ' ')} · page {candidate.source.page}</div><div className="mt-1 text-xs leading-5 text-foreground/70">“{candidate.source.evidence}”</div></button>)}</div>
    </div>
  </section>)}</div>;
}

function FieldTable({ value, onChange, compact = false }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void; compact?: boolean }) {
  const renameKey = (key: string, nextKey: string) => {
    const trimmed = nextKey.trim();
    if (!trimmed || trimmed === key) return;
    const next = { ...value };
    const fieldValue = next[key];
    delete next[key];
    next[trimmed] = fieldValue;
    onChange(next);
  };

  const updateValue = (key: string, nextValue: unknown) => onChange({ ...value, [key]: nextValue });
  const remove = (key: string) => {
    const next = { ...value };
    delete next[key];
    onChange(next);
  };
  const add = () => {
    let key = 'New field';
    let count = 2;
    while (key in value) key = `New field ${count++}`;
    onChange({ ...value, [key]: '' });
  };

  if (compact) {
    return <div className="space-y-2">
      {Object.entries(value).map(([key, fieldValue]) => <div key={key} className="rounded-md border border-[#e1dbcf] bg-[#fbfaf7] p-2.5">
        <div className="flex items-center gap-2"><input value={key} onChange={(event) => renameKey(key, event.target.value)} aria-label={`Field name ${key}`} className="h-8 min-w-0 flex-1 rounded border border-input bg-background px-2 text-xs font-medium outline-none focus:border-[#a17734] focus:ring-2 focus:ring-[#a17734]/15" /><button type="button" onClick={() => remove(key)} aria-label={`Remove ${key}`} className="rounded p-1 text-muted-foreground hover:bg-red-50 hover:text-red-700"><Trash2 size={14} /></button></div>
        <div className="mt-2 min-w-0"><ValueEditor value={fieldValue} onChange={(nextValue) => updateValue(key, nextValue)} /></div>
      </div>)}
      <button type="button" onClick={add} className="flex items-center gap-1.5 rounded border border-dashed border-[#c8a86d] px-2.5 py-1.5 text-[11px] font-semibold text-[#72501d] hover:bg-[#f8f1e3]"><Plus size={13} /> Add field</button>
    </div>;
  }

  return <div>
    <div className="grid grid-cols-[minmax(108px,.36fr)_minmax(0,.64fr)_28px] gap-3 border-b border-border px-2 pb-2 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground"><span>Field</span><span>Value</span><span /></div>
    <div className="divide-y divide-border/80">
      {Object.entries(value).map(([key, fieldValue]) => <div key={key} className="grid grid-cols-[minmax(108px,.36fr)_minmax(0,.64fr)_28px] items-start gap-3 px-2 py-2.5">
        <input value={key} onChange={(event) => renameKey(key, event.target.value)} aria-label={`Field name ${key}`} className="h-8 min-w-0 rounded border border-input bg-background px-2 text-xs font-medium outline-none focus:border-[#a17734] focus:ring-2 focus:ring-[#a17734]/15" />
        <div className="min-w-0"><ValueEditor value={fieldValue} onChange={(nextValue) => updateValue(key, nextValue)} /></div>
        <button type="button" onClick={() => remove(key)} aria-label={`Remove ${key}`} className="mt-1 rounded p-1 text-muted-foreground hover:bg-red-50 hover:text-red-700"><Trash2 size={14} /></button>
      </div>)}
    </div>
    <button type="button" onClick={add} className="mt-3 flex items-center gap-1.5 rounded border border-dashed border-[#c8a86d] px-2.5 py-1.5 text-[11px] font-semibold text-[#72501d] hover:bg-[#f8f1e3]"><Plus size={13} /> Add field</button>
  </div>;
}

function ValueEditor({ value, onChange }: { value: unknown; onChange: (value: unknown) => void }) {
  if (Array.isArray(value)) {
    const updateItem = (index: number, nextValue: unknown) => onChange(value.map((item, itemIndex) => itemIndex === index ? nextValue : item));
    return <div className="rounded border border-[#d8d2c4] bg-white p-2"><div className="mb-2 font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">List · {value.length} items</div><div className="space-y-2">{value.map((item, index) => <div key={index} className="flex gap-2 rounded border border-border bg-[#fbfaf7] p-2"><span className="pt-1 font-mono text-[10px] text-muted-foreground">{index + 1}</span><div className="min-w-0 flex-1"><ValueEditor value={item} onChange={(nextValue) => updateItem(index, nextValue)} /></div><button type="button" onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remove item ${index + 1}`} className="h-7 rounded p-1 text-muted-foreground hover:bg-red-50 hover:text-red-700"><Trash2 size={13} /></button></div>)}</div><button type="button" onClick={() => onChange([...value, ''])} className="mt-2 flex items-center gap-1 text-[11px] font-semibold text-[#72501d]"><Plus size={12} /> Add item</button></div>;
  }
  if (value && typeof value === 'object') {
    return <div className="rounded border border-[#d8d2c4] bg-white p-2"><div className="mb-2 font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground">Nested fields</div><FieldTable value={value as Record<string, unknown>} onChange={onChange} compact /></div>;
  }
  if (typeof value === 'boolean') {
    return <label className="flex h-8 items-center gap-2 rounded border border-input bg-background px-2 text-xs"><input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} /> {value ? 'Yes' : 'No'}</label>;
  }
  if (typeof value === 'number') {
    return <input type="number" value={value} onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))} aria-label="Field value" className="h-8 w-full rounded border border-input bg-background px-2 text-xs outline-none focus:border-[#a17734] focus:ring-2 focus:ring-[#a17734]/15" />;
  }
  return <textarea value={value == null ? '' : String(value)} onChange={(event) => onChange(event.target.value)} placeholder={value == null ? 'No value' : ''} aria-label="Field value" rows={String(value ?? '').length > 90 ? 3 : 1} className="w-full resize-y rounded border border-input bg-background px-2 py-1.5 text-xs leading-5 outline-none focus:border-[#a17734] focus:ring-2 focus:ring-[#a17734]/15" />;
}
