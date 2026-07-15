import ContactLinks from './ContactLinks.jsx';
import SignalBadge from './SignalBadge.jsx';

function initials(name) {
  return name.split(' ').filter(Boolean).slice(0, 2).map((p) => p[0]).join('');
}

export default function CandidateCard({ candidate, rank, onViewEvidence }) {
  const c = candidate;
  const arc = Math.min(100, c.score || 0);
  const schoolLine = [
    c.school && c.graduation_year ? `${c.school} '${String(c.graduation_year).slice(2)}` : c.school,
    c.area,
  ].filter(Boolean).join(' • ');
  const locationLine =
    c.origin_location && c.current_location && c.origin_location !== c.current_location
      ? `From ${c.origin_location} — now in ${c.current_location}`
      : c.current_location || c.origin_location;

  return (
    <div className="bg-card border border-line rounded-md px-10 py-8 max-w-2xl mx-auto">
      <div className="flex items-start justify-between">
        <span className="font-mono text-xs text-olive">#{String(rank).padStart(3, '0')}</span>
        <span className="label-mono">coverage {c.coverage}</span>
      </div>

      <div className="flex flex-col items-center text-center mt-2">
        <div className="relative w-24 h-24">
          <svg viewBox="0 0 100 100" className="absolute inset-0 -rotate-90">
            <circle cx="50" cy="50" r="47" fill="none" stroke="#E6E2D4" strokeWidth="2" />
            <circle
              cx="50" cy="50" r="47" fill="none" stroke="#6B6B32" strokeWidth="2.5"
              strokeDasharray={`${(arc / 100) * 295.3} 295.3`} strokeLinecap="round"
            />
          </svg>
          <div className="absolute inset-2 rounded-full bg-cream border border-line flex items-center justify-center">
            <span className="font-display text-2xl text-ink-soft">{initials(c.name)}</span>
          </div>
        </div>

        <h2 className="font-display text-4xl mt-4">{c.name}</h2>
        <p className="font-mono text-[11px] text-ink-faint mt-1.5">{schoolLine}</p>
        {locationLine && <p className="font-mono text-[11px] text-ink-faint mt-0.5">{locationLine}</p>}

        <p className="label-mono mt-6">signal score</p>
        <p className="font-mono text-5xl text-olive mt-1">{Math.round(c.score)}</p>

        {c.thesis && (
          <div className="mt-6 max-w-lg">
            <p className="label-mono mb-2">thesis</p>
            <p className="text-[15px] leading-relaxed text-ink-soft italic">{c.thesis}</p>
          </div>
        )}

        <div className="flex flex-wrap justify-center gap-2 mt-6">
          {(c.top_signals || []).map((s, i) => <SignalBadge key={i} signal={s} />)}
        </div>

        {c.connection_context && (
          <p className="mt-5 text-[13px] text-ink-soft">
            <span className="font-mono text-[10px] uppercase tracking-widest text-olive mr-2">orbit</span>
            {c.connection_context}
          </p>
        )}
        {c.warm_intro && (
          <p className="mt-1.5 text-[13px] text-ink-soft">
            <span className="font-mono text-[10px] uppercase tracking-widest text-olive mr-2">intro</span>
            {c.warm_intro}
          </p>
        )}

        <ContactLinks links={c.contact_links} className="mt-5 justify-center" />

        <div className="flex gap-3 mt-7">
          <button
            onClick={onViewEvidence}
            className="bg-olive hover:bg-olive-dark text-cream font-mono text-xs px-5 py-2.5 rounded-sm transition-colors"
          >
            VIEW EVIDENCE →
          </button>
        </div>
      </div>
    </div>
  );
}
