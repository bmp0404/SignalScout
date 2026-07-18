// Candidate counts by discovery_source (how a candidate was FOUND), distinct
// from SourceMix.jsx which shows signal-level provenance mix.
const SOURCE_COLORS = {
  pdl_discovery: '#4E5D3A',
  coresignal_discovery: '#7A6B4E',
  github: '#6B6B32',
  unspecified: '#8A8574',
};

const FALLBACK_COLORS = ['#5D6B6B', '#75664D', '#8A7A2E', '#6B4E5D', '#4E6B5D'];

function labelFor(source) {
  if (!source) return 'Unspecified';
  return source
    .split('_')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ');
}

function colorFor(source, index) {
  return SOURCE_COLORS[source] || FALLBACK_COLORS[index % FALLBACK_COLORS.length];
}

export default function SourceMixChart({ mix }) {
  if (!mix || Object.keys(mix).length === 0) {
    return (
      <div className="bg-card border border-line rounded-md px-5 py-4">
        <span className="label-mono">candidates by discovery source</span>
        <p className="text-sm text-ink-faint mt-2">No provider-discovered candidates yet.</p>
      </div>
    );
  }
  const entries = Object.entries(mix).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, count]) => sum + count, 0) || 1;

  return (
    <div className="bg-card border border-line rounded-md px-5 py-4">
      <div className="flex items-center justify-between mb-2">
        <span className="label-mono">candidates by discovery source</span>
        <span className="label-mono text-ink-faint">{total} candidates</span>
      </div>
      <div className="flex h-2 w-full overflow-hidden rounded-sm bg-line/40">
        {entries.map(([source, count], i) => (
          <div
            key={source}
            style={{ width: `${(100 * count) / total}%`, background: colorFor(source, i) }}
            title={`${labelFor(source)}: ${count} (${((100 * count) / total).toFixed(1)}%)`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3">
        {entries.map(([source, count], i) => (
          <span key={source} className="flex items-center gap-1.5 font-mono text-[10px] text-ink-soft">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: colorFor(source, i) }} />
            {labelFor(source)} · {count}
          </span>
        ))}
      </div>
    </div>
  );
}
