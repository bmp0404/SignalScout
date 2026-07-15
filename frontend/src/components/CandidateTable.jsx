import { useMemo, useState } from 'react';
import { CANDIDATE_VIEWS, filterCandidatesByView } from './candidateViews.js';
import SignalBadge, { sourceLabel } from './SignalBadge.jsx';

const UNKNOWN_FOLLOWER_CAP = 1000;

export default function CandidateTable({
  candidates,
  onSelect,
  highlightIds,
  defaultView = 'provider',
  defaultUnknownsOnly = true,
}) {
  const highlight = highlightIds instanceof Set ? highlightIds : new Set(highlightIds || []);
  const [sortKey, setSortKey] = useState('score');
  const [sortDesc, setSortDesc] = useState(true);
  const [areaFilter, setAreaFilter] = useState('All');
  const [sourceFilter, setSourceFilter] = useState('All');
  const [unknownsOnly, setUnknownsOnly] = useState(defaultUnknownsOnly);
  const [view, setView] = useState(defaultView);
  const discoveryViews = defaultView !== 'all';

  const areas = useMemo(
    () => ['All', ...new Set(candidates.map((c) => c.area).filter(Boolean))],
    [candidates],
  );

  const sources = useMemo(() => {
    const set = new Set();
    candidates.forEach((c) => Object.keys(c.source_counts || {}).forEach((s) => set.add(s)));
    return ['All', ...[...set].sort()];
  }, [candidates]);

  const rows = useMemo(() => {
    let filtered = filterCandidatesByView(candidates, view);
    filtered = areaFilter === 'All' ? filtered : filtered.filter((c) => c.area === areaFilter);
    if (sourceFilter !== 'All') {
      filtered = filtered.filter((c) => (c.source_counts || {})[sourceFilter] > 0);
    }
    if (unknownsOnly) {
      filtered = filtered.filter(
        (c) => c.github_followers == null || c.github_followers <= UNKNOWN_FOLLOWER_CAP,
      );
    }
    return [...filtered].sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
      return sortDesc ? -cmp : cmp;
    });
  }, [candidates, view, sortKey, sortDesc, areaFilter, sourceFilter, unknownsOnly]);

  const providerCount = candidates.filter(
    (candidate) => candidate.discovery_origin === 'provider_search',
  ).length;
  const crossSourceCount = candidates.filter(
    (candidate) => (candidate.source_diversity || 0) >= 2,
  ).length;

  const toggleSort = (key) => {
    if (key === sortKey) setSortDesc(!sortDesc);
    else {
      setSortKey(key);
      setSortDesc(true);
    }
  };

  return (
    <div>
      {discoveryViews && (
        <div className="flex flex-wrap items-center gap-2 mb-3" aria-label="Candidate view">
          {CANDIDATE_VIEWS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setView(value)}
              className={`px-3 py-1.5 border rounded-sm font-mono text-[10px] tracking-wider ${
                view === value
                  ? 'border-olive text-olive bg-olive/5'
                  : 'border-line text-ink-faint hover:text-ink-soft'
              }`}
            >
              {label.toUpperCase()}
            </button>
          ))}
          <span className="label-mono ml-auto">
            {providerCount} provider-discovered · {crossSourceCount} cross-source · {candidates.length} total
          </span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <span className="label-mono">area</span>
        <select
          value={areaFilter}
          onChange={(e) => setAreaFilter(e.target.value)}
          className="bg-card border border-line rounded-sm font-mono text-xs px-3 py-1.5 text-ink-soft"
        >
          {areas.map((a) => <option key={a}>{a}</option>)}
        </select>
        <span className="label-mono">source</span>
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          className="bg-card border border-line rounded-sm font-mono text-xs px-3 py-1.5 text-ink-soft"
        >
          {sources.map((s) => <option key={s} value={s}>{s === 'All' ? 'All' : sourceLabel(s)}</option>)}
        </select>
        <label className="flex items-center gap-1.5 label-mono cursor-pointer select-none">
          <input
            type="checkbox"
            checked={unknownsOnly}
            onChange={(e) => setUnknownsOnly(e.target.checked)}
            className="accent-olive"
          />
          unknowns only
        </label>
        <span className="label-mono ml-auto">{rows.length} candidates</span>
      </div>
      <div className="flex items-center justify-end gap-2 mb-3">
        <span className="label-mono">rank by</span>
        <button onClick={() => toggleSort('score')} className="font-mono text-[10px] text-olive underline">
          SCORE {sortKey === 'score' ? (sortDesc ? '↓' : '↑') : ''}
        </button>
        <button onClick={() => toggleSort('name')} className="font-mono text-[10px] text-ink-faint hover:text-olive">
          NAME {sortKey === 'name' ? (sortDesc ? '↓' : '↑') : ''}
        </button>
      </div>
      <div className="space-y-3">
        {rows.map((c, position) => (
          <article
            key={c.id}
            className={`bg-card border rounded-md px-5 sm:px-6 py-5 ${
              highlight.has(c.id) ? 'border-olive bg-olive/10' : 'border-line'
            }`}
          >
            <div className="flex items-start gap-4">
              <span className="font-mono text-sm text-olive mt-1">
                #{String(position + 1).padStart(3, '0')}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h2 className="font-display text-2xl">
                      {c.name}
                      {highlight.has(c.id) && (
                        <span className="ml-2 font-mono text-[9px] tracking-widest text-olive align-middle">NEW</span>
                      )}
                    </h2>
                    <p className="text-xs text-ink-faint mt-1">
                      {[c.school, c.area, c.region || c.current_location].filter(Boolean).join(' · ') || 'Public profile signals'}
                    </p>
                    {c.discovery_origin === 'provider_search' && (
                      <span
                        className={`inline-block mt-2 px-2 py-0.5 rounded-sm border font-mono text-[9px] uppercase tracking-wider ${
                          c.review_required
                            ? 'border-amber-300 text-amber-700 bg-amber-50'
                            : 'border-olive/40 text-olive bg-olive/5'
                        }`}
                      >
                        {c.review_required ? 'Needs review' : 'Verified'}
                      </span>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="font-mono text-2xl text-olive">{Math.round(c.score)}</p>
                    <p className="label-mono">{c.signal_count} signals</p>
                  </div>
                </div>
                {c.thesis && <p className="text-sm text-ink-soft mt-3 italic">{c.thesis}</p>}
                {c.source_counts && Object.keys(c.source_counts).length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 mt-3" aria-label={`Sources for ${c.name}`}>
                    <span className="label-mono text-ink-faint mr-1">sources</span>
                    {Object.entries(c.source_counts).map(([source, count]) => (
                      <span
                        key={source}
                        className="font-mono text-[9px] uppercase tracking-wider text-ink-faint border border-line rounded-sm px-1.5 py-0.5"
                      >
                        {sourceLabel(source)} {count}
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap gap-2 mt-4" aria-label={`Top signals for ${c.name}`}>
                  {(c.top_signals || []).map((signal, index) => (
                    <SignalBadge key={`${signal.type}-${index}`} signal={signal} />
                  ))}
                </div>
                {c.connection_context && (
                  <p className="text-xs text-ink-soft mt-3">
                    <span className="label-mono text-olive mr-2">orbit</span>
                    {c.connection_context}
                  </p>
                )}
                <button
                  onClick={() => onSelect(c)}
                  className="font-mono text-[10px] tracking-widest text-olive hover:text-olive-dark mt-4"
                >
                  VIEW FULL EVIDENCE →
                </button>
              </div>
            </div>
          </article>
        ))}
        {!rows.length && (
          <div className="bg-card border border-line rounded-md px-6 py-10 text-center">
            <p className="font-display text-xl">
              {view === 'provider'
                ? 'No provider discoveries yet.'
                : view === 'cross-source'
                  ? 'No cross-source candidates match these filters.'
                  : 'No candidates match these filters.'}
            </p>
            <p className="text-sm text-ink-faint mt-1">
              {view === 'provider'
                ? 'All candidates remain available. Provider results appear here only after a licensed search is configured and verified.'
                : 'Try another view or clear the area, source, and unknown-only filters.'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
