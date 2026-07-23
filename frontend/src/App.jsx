import { useEffect, useState } from 'react';
import { api } from './api/client.js';
import Backtest from './pages/Backtest.jsx';
import Digest from './pages/Digest.jsx';
import Discover from './pages/Discover.jsx';
import Pipeline from './pages/Pipeline.jsx';

const TABS = ['Discover', 'Backtest', 'Digest', 'Pipeline'];

export default function App() {
  const [tab, setTab] = useState('Discover');

  useEffect(() => {
    api.pageView({
      path: `/${tab.toLowerCase()}`,
      referrer: document.referrer || null,
    }).catch(() => {
      // Analytics must never affect the product experience.
    });
  }, [tab]);

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-cream/95 sticky top-0 z-10 backdrop-blur">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-4 flex flex-col sm:flex-row sm:items-end gap-4 justify-between">
          <div>
            <h1 className="font-display text-3xl leading-none">Signal Scout</h1>
            <p className="text-xs sm:text-sm text-ink-soft mt-1.5">
              Finding exceptional people before the world knows their names
            </p>
          </div>
          <nav className="flex gap-1 w-full sm:w-auto" aria-label="Primary">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 font-mono text-xs tracking-wide border rounded-sm transition-colors ${
                  tab === t
                    ? 'bg-olive text-cream border-olive'
                    : 'border-line text-ink-soft hover:border-olive hover:text-olive'
                }`}
              >
                {t.toUpperCase()}
              </button>
            ))}
          </nav>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
        {tab === 'Discover' && <Discover />}
        {tab === 'Backtest' && <Backtest />}
        {tab === 'Digest' && <Digest operatorMode />}
        {tab === 'Pipeline' && <Pipeline />}
      </main>
    </div>
  );
}
