function providerLabel(provider) {
  return provider === 'pdl' ? 'PDL' : provider === 'coresignal' ? 'Coresignal' : provider;
}

export default function CostDashboard({ summary }) {
  if (!summary) {
    return (
      <div className="bg-card border border-line rounded-md px-5 py-4">
        <span className="label-mono">provider cost summary</span>
        <p className="text-sm text-ink-faint mt-2">Loading…</p>
      </div>
    );
  }
  const providers = Object.entries(summary.provider_totals || {});

  return (
    <div className="bg-card border border-line rounded-md px-5 py-4">
      <span className="label-mono">provider cost summary</span>
      <div className="grid sm:grid-cols-2 gap-3 mt-3">
        {providers.map(([provider, totals]) => (
          <div key={provider} className="border border-line rounded-sm px-4 py-3">
            <p className="font-mono text-xs text-olive uppercase tracking-widest">{providerLabel(provider)}</p>
            <p className="font-display text-2xl mt-1">
              {totals.search_credits_used}
              <span className="text-sm text-ink-faint"> used</span>
            </p>
            <p className="font-mono text-[11px] text-ink-faint mt-0.5">
              {totals.search_credits_remaining} search credits remaining
            </p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 mt-4 pt-3 border-t border-dashed border-line font-mono text-[11px] text-ink-soft">
        <span>duplicates skipped: <span className="text-olive">{summary.duplicates_skipped}</span></span>
        <span>enrichment credits saved: <span className="text-olive">{summary.enrichment_credits_saved}</span></span>
      </div>
      {summary.recipe_totals?.length > 0 && (
        <div className="mt-4 pt-3 border-t border-dashed border-line">
          <p className="label-mono mb-2">by recipe</p>
          <div className="space-y-1">
            {summary.recipe_totals.map((r) => (
              <div key={r.recipe_id} className="flex justify-between font-mono text-[11px] text-ink-soft">
                <span>{r.recipe_id}</span>
                <span className="text-ink-faint">
                  {r.credit_units} credits
                  {(r.search_credit_units > 0 || r.collect_credit_units > 0)
                    ? ` (${r.search_credit_units} search / ${r.collect_credit_units} collect)`
                    : ''}
                  {' · '}{r.created} created · {r.duplicate_count + r.merged_count} deduped
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
