import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import CandidateCard from '../components/CandidateCard.jsx';
import CandidateTable from '../components/CandidateTable.jsx';
import DigestSignup from '../components/DigestSignup.jsx';
import EvidencePanel from '../components/EvidencePanel.jsx';
import SourceMix from '../components/SourceMix.jsx';

export default function Discover() {
  const [candidates, setCandidates] = useState([]);
  const [index, setIndex] = useState(0);
  const [browseAll, setBrowseAll] = useState(true);
  const [evidenceId, setEvidenceId] = useState(null);
  const [loadState, setLoadState] = useState('loading');
  const [sourceMix, setSourceMix] = useState(null);

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

  const loadSourceMix = () =>
    api.overview()
      .then((d) => setSourceMix(d.source_mix || null))
      .catch(() => {});

  useEffect(() => {
    // Fire both reads together on mount rather than in separate effects.
    Promise.all([loadCandidates(), loadSourceMix()]).catch(() => {});
  }, []);

  const current = candidates[index];

  return (
    <div>
      <DigestSignup />
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <p className="label-mono text-olive">Everyone discovered</p>
          <h2 className="font-display text-3xl mt-1">Discover</h2>
        </div>
        <button
          onClick={() => setBrowseAll(!browseAll)}
          className="font-mono text-xs text-olive hover:text-olive-dark"
        >
          {browseAll ? '← Card view' : 'Browse all →'}
        </button>
      </div>

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
      ) : !candidates.length ? (
        <div className="bg-card border border-line rounded-md px-6 py-10 text-center">
          <p className="font-display text-xl">No discoveries yet.</p>
          <p className="text-sm text-ink-faint mt-1">Run recipes on the Pipeline tab.</p>
        </div>
      ) : browseAll ? (
        <CandidateTable
          candidates={candidates}
          onSelect={(c) => setEvidenceId(c.id)}
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
          <div className="flex items-center justify-center gap-6 mt-6 font-mono text-xs">
            <button
              onClick={() => setIndex(Math.max(0, index - 1))}
              disabled={index === 0}
              className="text-ink-soft disabled:text-line hover:text-olive"
            >
              ← Previous
            </button>
            <span className="text-ink-faint">{index + 1} of {candidates.length}</span>
            <button
              onClick={() => setIndex(Math.min(candidates.length - 1, index + 1))}
              disabled={index === candidates.length - 1}
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
        />
      )}
    </div>
  );
}
