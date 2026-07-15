import { useState } from 'react';
import { api } from '../api/client.js';

const EMPTY_FORM = {
  email: '',
  frequency: 'daily',
  signalInterests: '',
  seedAccounts: '',
};

export default function DigestSignup() {
  const [form, setForm] = useState(EMPTY_FORM);
  const [status, setStatus] = useState('idle');
  const [message, setMessage] = useState('');
  const [subscription, setSubscription] = useState(null);

  const update = (field) => (event) => {
    setForm((current) => ({ ...current, [field]: event.target.value }));
    if (status === 'error') setStatus('idle');
  };

  const sendTestDigest = async (currentSubscription) => {
    setStatus('sending');
    setMessage('Building and sending your test digest…');
    try {
      const result = await api.sendTestDigest({
        email: currentSubscription.email,
        token: currentSubscription.subscriber_token,
      });
      setStatus('sent');
      setMessage(result.message);
    } catch (error) {
      if (error.status === 503) {
        setStatus('configuration');
        setMessage("Email delivery isn't configured yet.");
      } else if (error.status === 429) {
        setStatus('rate-limit');
        setMessage('A test digest was already sent recently. Please try again after 24 hours.');
      } else {
        setStatus('error');
        setMessage(error.message || "We couldn't send your test digest. Please try again.");
      }
    }
  };

  const subscribe = async (sendTest) => {
    if (!form.email.trim()) {
      setStatus('error');
      setMessage('Add an email address to join the digest.');
      return;
    }
    setStatus('subscribing');
    setMessage('');
    try {
      const result = await api.subscribe({
        email: form.email.trim(),
        frequency: form.frequency,
        signal_interests: form.signalInterests.trim(),
        seed_accounts: form.seedAccounts.trim(),
      });
      setSubscription(result);
      if (sendTest) {
        await sendTestDigest(result);
        return;
      }
      setStatus('success');
      setMessage(result.message);
    } catch {
      setStatus('error');
      setMessage('We could not save your signup. Check the email and try again.');
    }
  };

  const reset = () => {
    setForm(EMPTY_FORM);
    setSubscription(null);
    setMessage('');
    setStatus('idle');
  };

  const busy = status === 'subscribing' || status === 'sending';

  if (subscription) {
    const heading = status === 'sent' ? 'Check your inbox.' : 'Early signals, delivered.';
    return (
      <section className="bg-olive text-cream border border-olive-dark rounded-md px-6 py-5 mb-8">
        <p className="font-mono text-[10px] tracking-widest uppercase text-cream/70">Digest confirmed</p>
        <h2 className="font-display text-2xl mt-1">{heading}</h2>
        <p role="status" className="text-sm mt-2 text-cream/90">{message}</p>
        <div className="flex flex-col items-start sm:flex-row sm:items-center gap-3 mt-4">
          {status === 'success' && (
            <button
              type="button"
              onClick={() => sendTestDigest(subscription)}
              className="bg-cream text-olive-dark font-mono text-[10px] tracking-widest px-5 py-2.5 rounded-sm"
            >
              SEND ME A TEST DIGEST
            </button>
          )}
          {status === 'error' && (
            <button
              type="button"
              onClick={() => sendTestDigest(subscription)}
              className="bg-cream text-olive-dark font-mono text-[10px] tracking-widest px-5 py-2.5 rounded-sm"
            >
              TRY SENDING AGAIN
            </button>
          )}
          <button
            type="button"
            onClick={reset}
            disabled={busy}
            className="font-mono text-[10px] tracking-widest underline disabled:opacity-50"
          >
            USE ANOTHER EMAIL
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="bg-card border border-olive/60 rounded-md px-5 sm:px-6 py-5 mb-8">
      <div className="mb-4 sm:flex sm:items-end sm:justify-between sm:gap-6">
        <div>
          <p className="font-mono text-[10px] tracking-widest uppercase text-olive">Signal Scout digest</p>
          <h2 className="font-display text-2xl mt-1">Meet exceptional people before breakout.</h2>
        </div>
        <p className="text-sm text-ink-soft mt-1 sm:max-w-xs">
          Exact signals and direct profile links, delivered.
        </p>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          subscribe(false);
        }}
        className="grid gap-3"
      >
        <div className="grid sm:grid-cols-[1fr_140px] gap-3 items-end">
          <label>
            <span className="label-mono block mb-1">Email</span>
            <input
              type="email"
              value={form.email}
              onChange={update('email')}
              placeholder="you@firm.com"
              autoComplete="email"
              disabled={busy}
              className="w-full bg-cream border border-line rounded-sm px-3 py-2 text-sm focus:outline-none focus:border-olive"
            />
          </label>
          <label>
            <span className="label-mono block mb-1">Frequency</span>
            <select
              value={form.frequency}
              onChange={update('frequency')}
              disabled={busy}
              className="w-full bg-cream border border-line rounded-sm px-3 py-2 text-sm focus:outline-none focus:border-olive"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
            </select>
          </label>
        </div>
        <div className="flex flex-col sm:flex-row gap-3">
          <button
            type="submit"
            disabled={busy}
            className="bg-olive hover:bg-olive-dark disabled:bg-ink-faint text-cream font-mono text-[10px] tracking-widest px-5 py-2.5 rounded-sm h-[38px]"
          >
            {status === 'subscribing' ? 'SIGNING UP…' : 'JOIN THE DIGEST'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => subscribe(true)}
            className="border border-olive text-olive hover:bg-olive hover:text-cream disabled:border-ink-faint disabled:text-ink-faint font-mono text-[10px] tracking-widest px-5 py-2.5 rounded-sm h-[38px]"
          >
            {busy ? 'SENDING…' : 'SEND ME A TEST DIGEST'}
          </button>
        </div>
        <details className="group">
          <summary className="font-mono text-[10px] tracking-widest text-olive cursor-pointer">
            PERSONALIZE SIGNALS AND SEED ACCOUNTS
          </summary>
          <div className="grid sm:grid-cols-2 gap-3 mt-3">
            <label>
              <span className="label-mono block mb-1">Signals you care about · optional</span>
              <input
                value={form.signalInterests}
                onChange={update('signalInterests')}
                placeholder="AI research, open source traction"
                disabled={busy}
                className="w-full bg-cream border border-line rounded-sm px-3 py-2 text-sm focus:outline-none focus:border-olive"
              />
            </label>
            <label>
              <span className="label-mono block mb-1">Seed accounts · optional</span>
              <input
                value={form.seedAccounts}
                onChange={update('seedAccounts')}
                placeholder="GitHub, X, or provider profile URLs"
                disabled={busy}
                className="w-full bg-cream border border-line rounded-sm px-3 py-2 text-sm focus:outline-none focus:border-olive"
              />
            </label>
          </div>
        </details>
        {status === 'error' && (
          <p role="alert" className="font-mono text-[11px] text-red-600">{message}</p>
        )}
      </form>
    </section>
  );
}
