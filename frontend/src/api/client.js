function adminHeaders() {
  try {
    const secret = localStorage.getItem('ss_admin_secret');
    return secret ? { 'X-Admin-Secret': secret } : {};
  } catch {
    return {};
  }
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const resp = await fetch(path, { ...options, headers });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      // non-JSON error body — keep the status line
    }
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

export const api = {
  overview: () => request('/api/overview'),
  candidates: (cohort = 'discovery') => request(`/api/candidates?cohort=${cohort}`),
  candidate: (id) => request(`/api/candidates/${id}`),
  backtest: () => request('/api/backtest'),
  latestDigest: () => request('/api/digests/latest'),
  upcomingDigest: (offset = 0) => request(`/api/digest/upcoming?offset=${offset}`),
  generateDigest: () => request('/api/digests/generate', { method: 'POST', headers: adminHeaders() }),
  sendDigest: () => request('/api/digests/send', { method: 'POST', headers: adminHeaders() }),
  subscribe: (payload) => request('/api/subscribers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  pageView: (payload) => request('/api/analytics/page-view', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  discoveryRecipes: () => request('/api/discovery/recipes'),
  runRecipe: (id, limit) => request(
    `/api/discovery/recipes/${id}/run${limit ? `?limit=${limit}` : ''}`,
    { method: 'POST', headers: adminHeaders() },
  ),
  dryRunRecipe: (id, limit) => request(
    `/api/discovery/recipes/${id}/dry-run${limit ? `?limit=${limit}` : ''}`,
    { method: 'POST', headers: adminHeaders() },
  ),
  approveRecipe: (id) => request(`/api/discovery/recipes/${id}/approve`, { method: 'POST', headers: adminHeaders() }),
  discoveryCostSummary: () => request('/api/discovery/cost-summary'),
  reviewCandidate: (id, payload) => request(`/api/candidate-reviews/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
};
