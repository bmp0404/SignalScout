import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import CostDashboard from '../components/CostDashboard.jsx';
import SourceMixChart from '../components/SourceMixChart.jsx';

function providerLabel(provider) {
  if (provider === 'pdl') return 'PDL';
  if (provider === 'coresignal') return 'Coresignal';
  if (provider === 'exa') return 'Exa';
  return provider;
}

function outcomeBadge(r) {
  if (!r.provider_configured) {
    return { text: 'key missing', cls: 'border-amber-400 text-amber-600' };
  }
  if (!r.last_run || r.last_outcome === 'never_run') {
    return { text: 'never run', cls: 'border-line text-ink-faint' };
  }
  if (String(r.last_outcome || '').startsWith('error')) {
    return { text: 'error', cls: 'border-red-300 text-red-600' };
  }
  if (!r.last_created_count && !r.last_result_count) {
    return { text: 'no matches', cls: 'border-line text-ink-faint' };
  }
  return { text: `${r.last_created_count} new`, cls: 'border-olive text-olive' };
}

export default function Pipeline() {
  const [recipes, setRecipes] = useState(null);
  const [costSummary, setCostSummary] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [runSummary, setRunSummary] = useState(null);
  const [error, setError] = useState('');

  const loadAll = () => {
    setError('');
    Promise.all([
      api.discoveryRecipes(),
      api.discoveryCostSummary(),
    ])
      .then(([r, c]) => {
        setRecipes(r.recipes);
        setCostSummary(c);
      })
      .catch(() => setError('Pipeline data is unavailable. Try refreshing.'));
  };

  useEffect(() => {
    loadAll();
  }, []);

  const runRecipe = async (id, dryRun) => {
    setBusyId(id);
    setError('');
    setRunSummary(null);
    try {
      const summary = dryRun ? await api.dryRunRecipe(id) : await api.runRecipe(id);
      setRunSummary({ id, dryRun, ...summary });
      loadAll();
    } catch (err) {
      setError(
        err.status === 403
          ? `"${id}" needs approval before a real run — dry-run it or approve it first.`
          : `Could not ${dryRun ? 'dry-run' : 'run'} "${id}". Try again.`,
      );
    } finally {
      setBusyId(null);
    }
  };

  const approve = async (id) => {
    setBusyId(id);
    setError('');
    try {
      await api.approveRecipe(id);
      loadAll();
    } catch {
      setError(`Could not approve "${id}". Try again.`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <div className="flex items-end justify-between mb-6">
        <div>
          <p className="label-mono text-olive">Find more people</p>
          <h2 className="font-display text-3xl mt-1">Pipeline</h2>
          <p className="text-sm text-ink-soft mt-2 max-w-xl">
            Recipes run automatically in the background on their weekly/biweekly schedule.
            Use RUN only when you want extra people outside that cadence.
          </p>
        </div>
        <button
          onClick={loadAll}
          className="border border-line text-ink-faint font-mono text-xs px-4 py-2 rounded-sm hover:border-olive hover:text-olive"
        >
          REFRESH
        </button>
      </div>

      {error && (
        <div role="alert" className="border border-red-300 bg-red-50 rounded-sm px-4 py-3 mb-5">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4 mb-6">
        <CostDashboard summary={costSummary} />
        <SourceMixChart mix={costSummary?.candidates_by_discovery_source} />
      </div>

      {runSummary && (
        <div className="border border-olive/40 rounded-sm px-4 py-3 mb-5 font-mono text-[11px] text-ink-soft">
          {runSummary.dryRun ? 'Dry run' : 'Run'} of <span className="text-olive">{runSummary.id}</span>:{' '}
          {runSummary.provider_configured === false
            ? `no ${providerLabel(runSummary.provider)} API key configured — nothing ran, schedule untouched`
            : (!runSummary.dryRun && runSummary.reached_provider === false)
              ? 'provider not reached (budget exhausted) — schedule untouched, will retry'
              : `${runSummary.created} created, ${runSummary.duplicates} duplicates, ${runSummary.merged} merged, ${runSummary.credit_units} credits${runSummary.dry_run ? ' (none spent)' : ''}`}
        </div>
      )}

      <div className="bg-card border border-line rounded-md overflow-x-auto mb-6">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left font-mono text-[10px] uppercase tracking-widest text-ink-faint border-b border-line">
              <th className="px-4 py-3">Recipe</th>
              <th className="px-4 py-3">Provider</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Outcome</th>
              <th className="px-4 py-3">Frequency</th>
              <th className="px-4 py-3">Last run</th>
              <th className="px-4 py-3">Last result</th>
              <th className="px-4 py-3">Last credits</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {(recipes || []).map((r) => (
              <tr key={r.id} className="border-b border-line last:border-0">
                <td className="px-4 py-3">
                  <p className="font-medium">{r.name}</p>
                  <p className="font-mono text-[10px] text-ink-faint">{r.id} · {r.query_type}</p>
                </td>
                <td className="px-4 py-3 font-mono text-xs">{providerLabel(r.provider)}</td>
                <td className="px-4 py-3">
                  <span className={`font-mono text-[10px] uppercase tracking-widest px-2 py-0.5 rounded-sm border ${
                    r.approval_state === 'approved' ? 'border-olive text-olive' : 'border-line text-ink-faint'
                  }`}>
                    {r.approval_state}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {(() => {
                    const badge = outcomeBadge(r);
                    return (
                      <span className={`font-mono text-[10px] uppercase tracking-widest px-2 py-0.5 rounded-sm border ${badge.cls}`}>
                        {badge.text}
                      </span>
                    );
                  })()}
                </td>
                <td className="px-4 py-3 font-mono text-xs text-ink-soft">{r.frequency}</td>
                <td className="px-4 py-3 font-mono text-[11px] text-ink-faint">{r.last_run ? r.last_run.slice(0, 10) : 'never'}</td>
                <td className="px-4 py-3 font-mono text-[11px] text-ink-soft">
                  {r.last_created_count} new / {r.last_result_count} seen
                </td>
                <td className="px-4 py-3 font-mono text-[11px] text-ink-soft">{r.last_credit_units}</td>
                <td className="px-4 py-3">
                  <div className="flex gap-1.5 justify-end">
                    {r.approval_state !== 'approved' && (
                      <button
                        onClick={() => approve(r.id)}
                        disabled={busyId === r.id}
                        className="border border-line text-ink-faint font-mono text-[10px] px-2.5 py-1 rounded-sm hover:border-olive hover:text-olive disabled:opacity-50"
                      >
                        APPROVE
                      </button>
                    )}
                    <button
                      onClick={() => runRecipe(r.id, true)}
                      disabled={busyId === r.id}
                      className="border border-line text-ink-faint font-mono text-[10px] px-2.5 py-1 rounded-sm hover:border-olive hover:text-olive disabled:opacity-50"
                    >
                      DRY RUN
                    </button>
                    <button
                      onClick={() => runRecipe(r.id, false)}
                      disabled={busyId === r.id || r.approval_state !== 'approved'}
                      title={r.approval_state !== 'approved' ? 'Approve before running for real' : ''}
                      className="bg-olive hover:bg-olive-dark disabled:bg-ink-faint text-cream font-mono text-[10px] px-2.5 py-1 rounded-sm"
                    >
                      {busyId === r.id ? '…' : 'RUN'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {recipes && recipes.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-ink-faint">No recipes configured.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
