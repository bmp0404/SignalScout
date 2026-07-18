# frontend/core

Entry point, top-level app shell, and global styling — the files directly under `frontend/src/` (not `api/`, `components/`, or `pages/`).

## frontend/src/main.jsx
Bootstraps the React app by mounting `<App />` into the `#root` DOM node inside `React.StrictMode`, and imports the global stylesheet.

- No exported components or functions — this is the Vite/React entry script.

## frontend/src/App.jsx
Renders the page header/nav and switches between the four tabs, firing a page-view analytics beacon on every tab change.

- `TABS` — `['Discover', 'Backtest', 'Digest', 'Admin']`.
- `App()` — renders the sticky header (title, tagline, tab nav) and the active tab's page component (`Discover`, `Backtest`, `Digest`, or `DiscoveryAdmin`); on each tab change calls `api.pageView({ path, referrer })` (best-effort, errors are swallowed so analytics never break the UI).

## frontend/src/index.css
Global stylesheet: imports Tailwind's base/components/utilities layers, sets the page body font/colors, and defines the shared `.label-mono` utility class used for small uppercase mono labels across components.
