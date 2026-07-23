import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client.js';
import CandidateCard from '../components/CandidateCard.jsx';
import CandidateTable from '../components/CandidateTable.jsx';
import DigestSignup from '../components/DigestSignup.jsx';
import EvidencePanel from '../components/EvidencePanel.jsx';
import SourceMix from '../components/SourceMix.jsx';

const BUCKETS = [
  { id: 'unreviewed', label: 'Unreviewed' },
  { id: 'approved', label: 'Approved' },
  { id: 'rejected', label: 'Rejected' },
];

function reviewBucket(state) {
  if (state === 'approved') return 'approved';
  if (state === 'rejected') return 'rejected';
  return 'unreviewed';
}

export default function Discover() {
  const [candidates, setCandidates] = useState([]);
  const [index, setIndex] = useState(0);
  const [browseAll, setBrowseAll] = useState(true);
  const [evidenceId, setEvidenceId] = useState(null);
  const [bucket, setBucket] = useState('unreviewed');
  const [loadState, setLoadState] = useState('loading');
  const [sourceMix, setSourceMix] = useState(null);
  const [reviewBusyId, setReviewBusyId] = useState(null);
  const [reviewError, setReviewError] = useState('');

  const loadCandidates = () => {
    setLoadState('loading');
    return api.candidates('discovery').then((d) => {
      setCandidates(d.candidates);
      setIndex(0);
      setLoadState('success');
      return d.candidates;
    }).catch((error) => {
      setLoadState('error');
      throw error;
    });
  };

  const loadSourceMix = () => {
    api.overview()
      .then((d) => setSourceMix(d.source_mix || null))
      .catch(() => {});
  };

  useEffect(() => {
    loadCandidates().catch(() => {});
  }, []);

  useEffect(loadSourceMix, []);

  const counts = useMemo(() => {
    const next = { unreviewed: 0, approved: 0, rejected: 0 };
    for (const candidate of candidates) {
      next[reviewBucket(candidate.approval_state)] += 1;
    }
    return next;
  }, [candidates]);

  const filtered = useMemo(
    () => candidates.filter((c) => reviewBucket(c.approval_state) === bucket),
    [candidates, bucket],
  );

  useEffect(() => {
    setIndex(0);
  }, [bucket]);

  const reviewCandidate = async (personId, state) => {
    setReviewBusyId(personId);
    setReviewError('');
    try {
      await api.reviewCandidate(personId, { state });
      await loadCandidates();
    } catch {
      setReviewError('Could not update review state. Try again.');
    } finally {
      setReviewBusyId(null);
    }
  };

  const current = filtered[index];

  return (
    <div>
      <DigestSignup />
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <p className="label-mono text-olive">Review discoveries</p>
          <h2 className="font-display text-3xl mt-1">Discover</h2>
        </div>
        <button
          onClick={() => setBrowseAll(!browseAll)}
          className="font-mono text-xs text-olive hover:text-olive-dark"
        >
          {browseAll ? '← Card view' : 'Browse all →'}
        </button>
      </div>

      <div className="flex flex-wrap gap-1 mb-5" role="tablist" aria-label="Review buckets">
        {BUCKETS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={bucket === item.id}
            onClick={() => setBucket(item.id)}
            className={`px-3 py-1.5 border rounded-sm font-mono text-[10px] tracking-wider ${
              bucket === item.id
                ? 'border-olive text-olive bg-olive/5'
                : 'border-line text-ink-faint hover:text-ink-soft'
            }`}
          >
            {item.label.toUpperCase()} ({counts[item.id]})
          </button>
        ))}
      </div>

      {reviewError && (
        <div role="alert" className="border border-red-300 bg-red-50 rounded-sm px-4 py-3 mb-4">
          <p className="text-sm text-red-700">{reviewError}</p>
        </div>
      )}

      <SourceMix mix={sourceMix} />

      {loadState === 'loading' ? (
        <div className="bg-card border border-line rounded-md px-6 py-10 text-center">
          <p className="font-display text-xl">Ranking the latest signals…</p>
          <p className="text-sm text-ink-faint mt-1">This uses the evidence already stored in Signal Scout.</p>
        </div>
      ) : loadState === 'error' ? (
        <div role="alert" className="bg-card border border-line rounded-md px-6 py-10 text-center">
          <p className="font-display text-xl">The candidate list is unavailable.</p>
          <p className="text-sm text-ink-faint mt-1">The data is safe. Reconnect and try loading it again.</p>
          <button onClick={() => loadCandidates().catch(() => {})} className="mt-4 bg-olive text-cream font-mono text-[10px] tracking-widest px-4 py-2 rounded-sm">
            TRY AGAIN
          </button>
        </div>
      ) : !filtered.length ? (
        <div className="bg-card border border-line rounded-md px-6 py-10 text-center">
          <p className="font-display text-xl">No {bucket} candidates.</p>
          <p className="text-sm text-ink-faint mt-1">
            {bucket === 'unreviewed'
              ? 'Run recipes on the Pipeline tab to find more people.'
              : 'Switch buckets or review someone from Unreviewed.'}
          </p>
        </div>
      ) : browseAll ? (
        <CandidateTable
          key={bucket}
          candidates={filtered}
          onSelect={(c) => setEvidenceId(c.id)}
          onApprove={bucket !== 'approved' ? (c) => reviewCandidate(c.id, 'approved') : undefined}
          onReject={bucket !== 'rejected' ? (c) => reviewCandidate(c.id, 'rejected') : undefined}
          reviewBusyId={reviewBusyId}
          defaultView="all"
          defaultUnknownsOnly={false}
        />
      ) : (
        <>
          <CandidateCard
            candidate={current}
            rank={index + 1}
            onViewEvidence={() => setEvidenceId(current.id)}
          />
          <div className="flex items-center justify-center gap-3 mt-4">
            {bucket !== 'approved' && (
              <button
                type="button"
                disabled={reviewBusyId === current.id}
                onClick={() => reviewCandidate(current.id, 'approved')}
                className="bg-olive hover:bg-olive-dark disabled:bg-ink-faint text-cream font-mono text-[10px] tracking-widest px-4 py-1.5 rounded-sm"
              >
                APPROVE
              </button>
            )}
            {bucket !== 'rejected' && (
              <button
                type="button"
                disabled={reviewBusyId === current.id}
                onClick={() => reviewCandidate(current.id, 'rejected')}
                className="border border-line text-ink-soft font-mono text-[10px] tracking-widest px-4 py-1.5 rounded-sm hover:border-olive hover:text-olive disabled:opacity-50"
              >
                REJECT
              </button>
            )}
            {bucket !== 'unreviewed' && (
              <button
                type="button"
                disabled={reviewBusyId === current.id}
                onClick={() => reviewCandidate(current.id, 'unreviewed')}
                className="border border-line text-ink-faint font-mono text-[10px] tracking-widest px-4 py-1.5 rounded-sm hover:border-olive hover:text-olive disabled:opacity-50"
              >
                UNREVIEW
              </button>
            )}
          </div>
          <div className="flex items-center justify-center gap-6 mt-6 font-mono text-xs">
            <button
              onClick={() => setIndex(Math.max(0, index - 1))}
              disabled={index === 0}
              className="text-ink-soft disabled:text-line hover:text-olive"
            >
              ← Previous
            </button>
            <span className="text-ink-faint">{index + 1} of {filtered.length}</span>
            <button
              onClick={() => setIndex(Math.min(filtered.length - 1, index + 1))}
              disabled={index === filtered.length - 1}
              className="text-ink-soft disabled:text-line hover:text-olive"
            >
              Next →
            </button>
          </div>
        </>
      )}

      {evidenceId && (
        <EvidencePanel
          personId={evidenceId}
          onClose={() => setEvidenceId(null)}
          onApprove={() => reviewCandidate(evidenceId, 'approved')}
          onReject={() => reviewCandidate(evidenceId, 'rejected')}
          onUnreview={() => reviewCandidate(evidenceId, 'unreviewed')}
          reviewBusy={reviewBusyId === evidenceId}
        />
      )}
    </div>
  );
}
