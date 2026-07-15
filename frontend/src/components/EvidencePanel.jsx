import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import ContactLinks from './ContactLinks.jsx';
import SignalTimeline from './SignalTimeline.jsx';
import { sourceLabel } from './SignalBadge.jsx';

export default function EvidencePanel({ personId, onClose }) {
  const [profile, setProfile] = useState(null);
  const [state, setState] = useState('loading');

  const load = () => {
    setState('loading');
    api.candidate(personId)
      .then((result) => {
        setProfile(result);
        setState('success');
      })
      .catch(() => setState('error'));
  };

  useEffect(load, [personId]);

  if (state !== 'success' || !profile) {
    return (
      <div className="fixed inset-0 bg-ink/30 z-20 flex items-center justify-center">
        <div className="bg-cream border border-line rounded-md max-w-sm mx-4 px-6 py-6 text-center">
          {state === 'loading' ? (
            <p className="font-mono text-xs text-ink-faint">Loading the evidence receipt…</p>
          ) : (
            <>
              <p className="font-display text-xl">Evidence is temporarily unavailable.</p>
              <p className="text-sm text-ink-faint mt-1">The candidate has not been removed.</p>
              <div className="flex justify-center gap-4 mt-4">
                <button onClick={load} className="font-mono text-[10px] text-olive underline">TRY AGAIN</button>
                <button onClick={onClose} className="font-mono text-[10px] text-ink-faint">CLOSE</button>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  const b = profile.breakdown;
  return (
    <div className="fixed inset-0 bg-ink/30 z-20 overflow-y-auto py-10 px-4" onClick={onClose}>
      <div
        className="bg-cream border border-line rounded-md max-w-3xl mx-auto p-8"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 className="font-display text-3xl">{profile.name}</h2>
            <p className="font-mono text-[11px] text-ink-faint mt-1">
              {[profile.school, profile.area, profile.region].filter(Boolean).join(' • ')}
            </p>
          </div>
          <button onClick={onClose} className="font-mono text-xs text-ink-faint hover:text-ink">CLOSE ✕</button>
        </div>

        <ContactLinks links={profile.contact_links} className="mt-3" />

        {profile.source_counts && Object.keys(profile.source_counts).length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 mt-4">
            <span className="label-mono text-ink-faint mr-1">evidence sources</span>
            {Object.entries(profile.source_counts).map(([source, count]) => (
              <span
                key={source}
                className="font-mono text-[10px] uppercase tracking-wider text-ink-soft border border-line rounded-sm px-2 py-0.5"
              >
                {sourceLabel(source)} · {count}
              </span>
            ))}
          </div>
        )}

        <h3 className="label-mono mt-8 mb-3">score receipt — {Math.round(profile.score)} / 100</h3>
        <div className="bg-card border border-line rounded-md overflow-hidden">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="border-b border-line">
                <th className="text-left px-4 py-2 label-mono">Evidence</th>
                <th className="text-left px-4 py-2 label-mono">Date</th>
                <th className="text-left px-4 py-2 label-mono">Source</th>
                <th className="text-right px-4 py-2 label-mono">Strength × Weight</th>
                <th className="text-right px-4 py-2 label-mono">Points</th>
              </tr>
            </thead>
            <tbody>
              {b.items.map((item, i) => (
                <tr key={i} className="border-b border-line-soft last:border-0">
                  <td className="px-4 py-2">
                    {item.source_url ? (
                      <a href={item.source_url} target="_blank" rel="noreferrer" className="text-olive hover:underline">
                        {item.label}
                      </a>
                    ) : item.label}
                  </td>
                  <td className="px-4 py-2 font-mono text-ink-faint">{item.date}</td>
                  <td className="px-4 py-2 font-mono text-ink-faint">{item.source}</td>
                  <td className="px-4 py-2 font-mono text-right">{item.strength} × {item.weight}</td>
                  <td className="px-4 py-2 font-mono text-right text-olive">{item.points}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-4 py-3 border-t border-line font-mono text-[11.5px] text-ink-soft bg-cream/60">
            raw {b.raw} + recency {b.recency_bonus} → × diversity {b.diversity_multiplier} × age {b.age_factor} = {b.adjusted} (normalized to {Math.round(profile.score)})
          </div>
        </div>

        <h3 className="label-mono mt-8 mb-3">signal timeline</h3>
        <SignalTimeline timeline={profile.timeline} breakout={profile.breakout_date} />

        {profile.connections?.length > 0 && (
          <>
            <h3 className="label-mono mt-8 mb-3">network</h3>
            <ul className="space-y-1.5">
              {profile.connections.map((conn, i) => (
                <li key={i} className="text-[13px] text-ink-soft">
                  <span className="font-mono text-[10px] text-olive uppercase tracking-widest mr-2">{conn.edge_type.replaceAll('_', ' ')}</span>
                  {conn.description}
                  <span className="font-mono text-[10px] text-ink-faint ml-2">{conn.observed_date}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
