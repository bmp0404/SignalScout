import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import ContactLinks from '../components/ContactLinks.jsx';
import DigestSignup from '../components/DigestSignup.jsx';

export default function Digest({ operatorMode = false }) {
  const [digest, setDigest] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sendReceipt, setSendReceipt] = useState(null);
  const [loadState, setLoadState] = useState('loading');
  const [error, setError] = useState('');

  const loadLatest = () => {
    setLoadState('loading');
    setError('');
    api.latestDigest()
      .then((d) => {
        setDigest(d.digest);
        setLoadState('success');
      })
      .catch(() => {
        setLoadState('error');
        setError('The latest digest could not be loaded. Try again in a moment.');
      });
  };

  useEffect(() => {
    if (operatorMode) loadLatest();
    else setLoadState('public');
  }, [operatorMode]);

  const generate = async () => {
    setBusy(true);
    setSendReceipt(null);
    setError('');
    try {
      const d = await api.generateDigest();
      setDigest(d.digest);
    } catch {
      setError('The digest could not be generated. Check that ranked discoveries are available, then retry.');
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
    } catch {
      setError('The send could not be completed. Your subscriber list may not have been contacted.');
    } finally {
      setBusy(false);
    }
  };

  if (!operatorMode) {
    return (
      <div className="max-w-2xl mx-auto">
        <DigestSignup />
        <section className="bg-card border border-line rounded-md px-6 py-8">
          <p className="label-mono text-olive">Subscriber digest</p>
          <h2 className="font-display text-2xl mt-2">Reviewed signals, delivered directly.</h2>
          <p className="text-sm text-ink-soft mt-3">
            Generation, previews, and sends remain restricted to the server-side review workflow.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      <DigestSignup />
      <div className="flex items-end justify-between mb-6">
        <div>
          <h2 className="font-display text-3xl">
            {digest ? `${digest.entries.length} people you should know` : 'The digest'}
          </h2>
          <p className="label-mono mt-1.5">
            {digest ? digest.generated_at.slice(0, 10) : 'not generated yet'} · evidence and available profile links
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={generate}
            disabled={busy}
            className="bg-olive hover:bg-olive-dark disabled:opacity-50 text-cream font-mono text-xs px-4 py-2 rounded-sm"
          >
            {busy ? 'GENERATING…' : digest ? 'REGENERATE' : 'GENERATE'}
          </button>
          <button
            onClick={send}
            disabled={busy}
            className="border border-line text-ink-faint font-mono text-xs px-4 py-2 rounded-sm hover:border-olive hover:text-olive disabled:opacity-40"
            title="Send the current approved picks to all active subscribers now"
          >
            {busy ? 'WORKING…' : 'SEND TO SUBSCRIBERS'}
          </button>
        </div>
      </div>

      {sendReceipt && (
        <p className="font-mono text-[11px] text-olive border border-olive/40 rounded-sm px-3 py-2 mb-5">
          Sent to {sendReceipt.sent_count} of {sendReceipt.subscriber_count} active subscriber
          {sendReceipt.subscriber_count === 1 ? '' : 's'}
          {sendReceipt.empty_count ? ` · ${sendReceipt.empty_count} had no new people` : ''}
          {sendReceipt.subscriber_count === 0 ? ' · no active subscribers yet' : ''}.
        </p>
      )}

      {error && (
        <div role="alert" className="border border-red-300 bg-red-50 rounded-sm px-4 py-3 mb-5">
          <p className="text-sm text-red-700">{error}</p>
          {loadState === 'error' && (
            <button onClick={loadLatest} className="font-mono text-[10px] tracking-widest text-red-700 underline mt-1">
              TRY AGAIN
            </button>
          )}
        </div>
      )}

      {loadState === 'loading' && (
        <p className="text-ink-faint italic">Loading the latest digest…</p>
      )}
      {loadState === 'success' && !digest && (
        <div className="bg-card border border-line rounded-md px-6 py-8 text-center">
          <p className="font-display text-xl">No digest has been generated yet.</p>
          <p className="text-sm text-ink-faint mt-1">Generate a preview from the current ranked discoveries.</p>
        </div>
      )}

      {digest?.entries.map((entry, i) => (
        <div key={entry.person_id} className="bg-card border border-line rounded-md px-7 py-6 mb-4">
          <div className="flex items-start justify-between">
            <span className="font-mono text-[11px] text-olive">#{String(i + 1).padStart(3, '0')}</span>
            <span className="font-mono text-xl text-olive">{Math.round(entry.score)}</span>
          </div>
          <h3 className="font-display text-2xl mt-1">{entry.name}</h3>
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
