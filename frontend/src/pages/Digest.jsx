import { useRef, useState } from 'react';
import { api } from '../api/client.js';
import ContactLinks from '../components/ContactLinks.jsx';
import DigestSignup from '../components/DigestSignup.jsx';
import AdminOnly from '../components/AdminOnly.jsx';
import { useAsyncData } from '../hooks/useAsyncData.js';

const CADENCE_LABELS = {
  daily: 'daily',
  every_3_days: 'every 3 days',
  weekly: 'weekly',
};

function autoSendSummary(auto) {
  if (!auto) return '';
  const cadence = CADENCE_LABELS[auto.default_cadence] || 'every 3 days';
  const parts = [`Sends automatically ${cadence}`];
  if (auto.active_subscribers) {
    parts.push(`${auto.active_subscribers} active subscriber${auto.active_subscribers === 1 ? '' : 's'}`);
  } else {
    parts.push('no active subscribers yet');
  }
  if (auto.last_sent_at) {
    parts.push(`last sent ${auto.last_sent_at.slice(0, 10)}`);
  } else {
    parts.push('no digest sent yet');
  }
  return parts.join(' · ');
}

export default function Digest() {
  const [busy, setBusy] = useState(false);
  const [sendReceipt, setSendReceipt] = useState(null);
  const [error, setError] = useState('');
  // Pagination cursor: each Refresh advances to the next batch (the server wraps
  // around the pool). A ref so the useAsyncData loader reads the latest value.
  const offsetRef = useRef(0);
  const {
    data: digest,
    state: loadState,
    reload,
  } = useAsyncData(() => api.upcomingDigest(offsetRef.current));

  const refresh = async () => {
    setError('');
    setSendReceipt(null);
    setBusy(true);
    if (digest && Number.isInteger(digest.next_offset)) {
      offsetRef.current = digest.next_offset;
    }
    try {
      await reload();
    } catch {
      setError('The upcoming digest could not be refreshed. Try again in a moment.');
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    setError('');
    setSendReceipt(null);
    setBusy(true);
    try {
      const d = await api.sendDigest();
      setSendReceipt(d.summary);
      await reload().catch(() => {});
    } catch {
      setError('The send could not be completed. Your subscriber list may not have been contacted.');
    } finally {
      setBusy(false);
    }
  };

  const entries = digest?.entries || [];
  const auto = digest?.auto_send;

  return (
    <div className="max-w-2xl mx-auto">
      <DigestSignup />
      <div className="flex items-end justify-between mb-3">
        <div>
          <h2 className="font-display text-3xl">
            {entries.length ? `${entries.length} people up next` : 'The digest'}
          </h2>
          <p className="label-mono mt-1.5">
            what subscribers receive next · evidence and available profile links
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={refresh}
            disabled={busy}
            title="Show the next batch of people from the pool"
            className="border border-line text-ink-faint font-mono text-xs px-4 py-2 rounded-sm hover:border-olive hover:text-olive disabled:opacity-40"
          >
            {busy ? 'WORKING…' : 'NEW BATCH'}
          </button>
          <AdminOnly>
            <button
              onClick={send}
              disabled={busy}
              className="bg-olive hover:bg-olive-dark disabled:opacity-50 text-cream font-mono text-xs px-4 py-2 rounded-sm"
              title="Send the current picks to all active subscribers now"
            >
              {busy ? 'WORKING…' : 'SEND NOW'}
            </button>
          </AdminOnly>
        </div>
      </div>

      {auto && (
        <p className="font-mono text-[11px] text-olive border border-olive/40 rounded-sm px-3 py-2 mb-5">
          {autoSendSummary(auto)}
        </p>
      )}

      {sendReceipt && (
        <p className="font-mono text-[11px] text-olive border border-olive/40 rounded-sm px-3 py-2 mb-5">
          Sent to {sendReceipt.sent_count} of {sendReceipt.subscriber_count} active subscriber
          {sendReceipt.subscriber_count === 1 ? '' : 's'}
          {sendReceipt.empty_count ? ` · ${sendReceipt.empty_count} had no new people` : ''}
          {sendReceipt.subscriber_count === 0 ? ' · no active subscribers yet' : ''}.
        </p>
      )}

      {(error || loadState === 'error') && (
        <div role="alert" className="border border-red-300 bg-red-50 rounded-sm px-4 py-3 mb-5">
          <p className="text-sm text-red-700">
            {error || 'The upcoming digest could not be loaded. Try again in a moment.'}
          </p>
          {loadState === 'error' && (
            <button onClick={() => reload().catch(() => {})} className="font-mono text-[10px] tracking-widest text-red-700 underline mt-1">
              TRY AGAIN
            </button>
          )}
        </div>
      )}

      {loadState === 'loading' && (
        <p className="text-ink-faint italic">Loading the upcoming digest…</p>
      )}
      {loadState === 'success' && !entries.length && (
        <div className="bg-card border border-line rounded-md px-6 py-8 text-center">
          <p className="font-display text-xl">Everyone available has already been featured.</p>
          <p className="text-sm text-ink-faint mt-1">Approve more candidates in Discover, or run the Pipeline to surface new people.</p>
        </div>
      )}

      {entries.map((entry, i) => (
        <div key={entry.person_id} className="bg-card border border-line rounded-md px-7 py-6 mb-4">
          <div className="flex items-start justify-between">
            <span className="font-mono text-[11px] text-olive">#{String(i + 1).padStart(3, '0')}</span>
            <span className="font-mono text-xl text-olive">{Math.round(entry.score)}</span>
          </div>
          <h3 className="font-display text-2xl mt-1">
            {entry.name}
            {entry.provisional && (
              <span className="ml-2 align-middle font-mono text-[9px] tracking-widest uppercase text-amber-700 border border-amber-300 rounded-sm px-1.5 py-0.5">
                provisional
              </span>
            )}
          </h3>
          <p className="font-mono text-[11px] text-ink-faint mt-0.5">
            {entry.school_line}{entry.location_line ? ` · ${entry.location_line}` : ''}
          </p>
          <p className="text-[14px] text-ink-soft leading-relaxed mt-3">{entry.thesis}</p>
          <div className="flex flex-wrap gap-2 mt-3">
            {entry.top_signals.map((t, j) => (
              <span key={j} className="border border-line rounded-sm px-2.5 py-1 font-mono text-[10.5px] text-ink-soft">{t}</span>
            ))}
          </div>
          {entry.connection_context && (
            <p className="text-[13px] text-ink-soft mt-3">
              <span className="font-mono text-[10px] uppercase tracking-widest text-olive mr-2">orbit</span>
              {entry.connection_context}
            </p>
          )}
          {entry.warm_intro && (
            <p className="text-[13px] text-ink-soft mt-1">
              <span className="font-mono text-[10px] uppercase tracking-widest text-olive mr-2">intro</span>
              {entry.warm_intro}
            </p>
          )}
          {entry.why_now && (
            <p className="text-[13px] mt-3 pl-3 border-l-2 border-olive text-ink-soft">{entry.why_now}</p>
          )}
          <ContactLinks links={entry.contact_links} className="mt-4 pt-3 border-t border-dashed border-line" />
        </div>
      ))}
    </div>
  );
}
