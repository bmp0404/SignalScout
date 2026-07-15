import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';
import CandidateCard from '../components/CandidateCard.jsx';
import CandidateTable from '../components/CandidateTable.jsx';
import DigestSignup from '../components/DigestSignup.jsx';
import EvidencePanel from '../components/EvidencePanel.jsx';
import PipelineProgress from '../components/PipelineProgress.jsx';
import SourceMix from '../components/SourceMix.jsx';

const POLL_MS = 1200;

export default function Discover({ showOperatorControls = false }) {
  const [candidates, setCandidates] = useState([]);
  const [index, setIndex] = useState(0);
  const [browseAll, setBrowseAll] = useState(true);
  const [evidenceId, setEvidenceId] = useState(null);
  const [cohort, setCohort] = useState('discovery');
  const [loadState, setLoadState] = useState('loading');
  const [sourceMix, setSourceMix] = useState(null);

  const [jobStatus, setJobStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState(null);
  const [newIds, setNewIds] = useState(new Set());
  const pollRef = useRef(null);

  const loadCandidates = (which) => {
    setLoadState('loading');
    return api.candidates(which).then((d) => {
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
    loadCandidates(cohort).catch(() => {});
  }, [cohort]);

  useEffect(loadSourceMix, []);

  useEffect(() => () => clearInterval(pollRef.current), []);

  const onComplete = async (priorIds) => {
    setRunning(false);
    setCohort('discovery');
    const fresh = await loadCandidates('discovery').catch(() => []);
    setNewIds(new Set(fresh.filter((c) => !priorIds.has(c.id)).map((c) => c.id)));
    setBrowseAll(true);
    loadSourceMix();
  };

  const runDiscovery = async () => {
    setRunError(null);
    setNewIds(new Set());
    const priorIds = new Set(candidates.map((c) => c.id));
    try {
      const res = await api.runDiscovery();
      setJobStatus(res.status);
      setRunning(true);
      clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.discoveryStatus();
          setJobStatus(status);
          if (status.state === 'done' || status.state === 'error') {
            clearInterval(pollRef.current);
            if (status.state === 'done') onComplete(priorIds);
            else {
              setRunning(false);
              setRunError('Discovery stopped before completing. Check the service logs, then try again.');
            }
          }
        } catch (err) {
          clearInterval(pollRef.current);
          setRunning(false);
          setRunError('Live progress was interrupted. Try the discovery run again.');
        }
      }, POLL_MS);
    } catch (err) {
      setRunError(
        err.status === 400
          ? 'Live discovery needs a GitHub token. Add GITHUB_TOKEN to the service, then retry.'
          : err.status === 409
            ? 'A discovery run is already in progress. Wait a moment and retry.'
            : 'Discovery could not start right now. Please try again.',
      );
    }
  };

  const current = candidates[index];

  return (
    <div>
      <DigestSignup />
      <div className="flex items-center justify-between mb-6">
        {showOperatorControls && <div className="flex gap-1">
          {[['discovery', 'DISCOVERIES'], ['founder', 'GROUND TRUTH']].map(([value, label]) => (
            <button
              key={value}
              onClick={() => setCohort(value)}
              className={`px-3 py-1 font-mono text-[10px] tracking-widest border rounded-sm ${
                cohort === value ? 'border-olive text-olive' : 'border-line text-ink-faint hover:text-ink-soft'
              }`}
            >
              {label}
            </button>
          ))}
        </div>}
        <div className="flex items-center gap-4">
          {showOperatorControls && <button
            onClick={runDiscovery}
            disabled={running}
            className="bg-olive hover:bg-olive-dark disabled:bg-ink-faint text-cream font-mono text-[10px] tracking-widest px-4 py-1.5 rounded-sm transition-colors"
          >
            {running ? 'RUNNING…' : 'RUN DISCOVERY'}
          </button>}
          <button
            onClick={() => setBrowseAll(!browseAll)}
            className="font-mono text-xs text-olive hover:text-olive-dark"
          >
            {browseAll ? '← Card view' : 'Browse all →'}
          </button>
        </div>
      </div>

      {showOperatorControls && runError && (
        <div role="alert" className="border border-red-300 bg-red-50 rounded-sm px-4 py-3 mb-4 text-center">
          <p className="text-sm text-red-700">{runError}</p>
          <button onClick={runDiscovery} className="font-mono text-[10px] tracking-widest text-red-700 underline mt-1">
            RETRY
          </button>
        </div>
      )}
      {showOperatorControls && <PipelineProgress status={jobStatus} />}
      {cohort === 'discovery' && <SourceMix mix={sourceMix} />}

      {loadState === 'loading' ? (
        <div className="bg-card border border-line rounded-md px-6 py-10 text-center">
          <p className="font-display text-xl">Ranking the latest signals…</p>
          <p className="text-sm text-ink-faint mt-1">This uses the evidence already stored in Signal Scout.</p>
        </div>
      ) : loadState === 'error' ? (
        <div role="alert" className="bg-card border border-line rounded-md px-6 py-10 text-center">
          <p className="font-display text-xl">The candidate list is unavailable.</p>
          <p className="text-sm text-ink-faint mt-1">The data is safe. Reconnect and try loading it again.</p>
          <button onClick={() => loadCandidates(cohort).catch(() => {})} className="mt-4 bg-olive text-cream font-mono text-[10px] tracking-widest px-4 py-2 rounded-sm">
            TRY AGAIN
          </button>
        </div>
      ) : !candidates.length ? (
        <div className="bg-card border border-line rounded-md px-6 py-10 text-center">
          <p className="font-display text-xl">No ranked {cohort === 'discovery' ? 'discoveries' : 'founders'} yet.</p>
          <p className="text-sm text-ink-faint mt-1">
            {cohort === 'discovery'
              ? 'Run the live discovery pipeline after adding a GitHub token, or migrate the existing discovery database.'
              : 'Initialize the seed set to load the backtest founders.'}
          </p>
        </div>
      ) : browseAll ? (
        <CandidateTable
          key={cohort}
          candidates={candidates}
          onSelect={(c) => setEvidenceId(c.id)}
          highlightIds={newIds}
          defaultView={cohort === 'discovery' ? 'provider' : 'all'}
          defaultUnknownsOnly={cohort === 'discovery'}
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

      {evidenceId && <EvidencePanel personId={evidenceId} onClose={() => setEvidenceId(null)} />}
    </div>
  );
}
