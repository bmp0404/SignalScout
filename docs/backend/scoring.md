# Scoring

The scoring module turns a person's collected `Signal` records into a single comparable score: it computes a raw weighted sum per person, applies diversity/recency/age adjustments, then normalizes every score (founders, controls, and live discoveries) onto the same 0-100 scale calibrated against known founders' pre-breakout evidence. It also backtests the formula against ground-truth founders and controls to report recall and false-positive rates.

## backend/scoring/__init__.py
Empty package marker file; no code.

## backend/scoring/engine.py
Implements `ScoringEngine`, the core formula that converts a person's signals (plus graph edges to seed founders) into an itemized, adjusted, and normalized score.

- `ScoreBreakdown` — dataclass holding the itemized scoring receipt: `raw` total, per-signal `items`, `diversity_multiplier`, `recency_bonus`, `age_factor`, and final `adjusted` score.
- `ScoringEngine` — computes and normalizes founder-likeness scores from signals and graph edges.
  - `ScoringEngine.__init__(recency_window_days=730)` — stores the recency window (default 2 years) used to decide which signals count toward the recency bonus.
  - `ScoringEngine.connection_signal(person, edges, seed_ids, as_of) -> Signal | None` — derives a synthetic `connected_to_seeds` signal from graph edges to known seed founders, with strength scaling by number of distinct seeds touched, the best edge quality (e.g. co-authorship beats a follow, repeat co-authorship/hackathon-teammate edges count as top quality, and a seed following the person counts higher than being followed), plus a bonus for multiple independent relationship surfaces (discovery cohort only).
  - `ScoringEngine.compute(person, signals, as_of) -> ScoreBreakdown` — for each signal dated on/before `as_of`, multiplies `signal_strength` (0-1) by the signal type's weight (`weights.weight_for`) to get points and sums them into `raw`; counts distinct signal categories to build a `diversity` multiplier (`1 + DIVERSITY_BONUS_PER_CATEGORY * (categories - 1)`); computes a `recency_bonus` as `RECENCY_BONUS_PER_SIGNAL * min(recent_signal_count, RECENCY_BONUS_CAP) * raw`, where `recent` counts signals within `recency_window_days` of `as_of`; looks up an `age` multiplier via `weights.age_factor(person.graduation_year, as_of.year)`; and combines them as `adjusted = (raw + recency_bonus) * diversity * age`.
  - `ScoringEngine.normalize(adjusted_scores) -> dict[str, float]` (static) — min-max normalizes a dict of adjusted scores to 0-100 relative to the single max value; deprecated for the pitch path because one dominant outlier pins at 100 and compresses everyone else, kept only for callers that explicitly want max-relative scaling.
  - `ScoringEngine.reference_from(values, top_n=10) -> float` (static) — computes an outlier-robust reference scale as the median of the top-N positive adjusted scores, so a single 2x outlier can't define "100".
  - `ScoringEngine.normalize_calibrated(adjusted_scores, reference) -> dict[str, float]` (static) — scales each adjusted score against a fixed `reference` as `min(100, 100 * adjusted / reference)`, producing directly comparable scores across founders, controls, and discoveries on one absolute scale.
  - `ScoringEngine._parse(iso) -> date` (static) — parses an ISO date string (first 10 chars) into a `date`.

## backend/scoring/weights.py
Defines the hand-tuned signal-type weight table and the age/diversity/recency tuning constants that `engine.py` reads.

- `WEIGHTS: dict[str, float]` — per-signal-type point weights grouped by category: competition (e.g. `imo_medal`/`ioi_medal` 10.0, `usaco_camp` 9.0, `usaco_platinum` 8.0, `usamo_qualifier` 7.0, `usaco_gold` 6.0, `physics_olympiad` 6.0, `aime_qualifier` 4.0, `amc_high_score` 3.0), research (`regeneron_sts_finalist` 8.0, `cited_paper` 8.0, `isef_award`/`science_fair_win`/`co_authored_paper` 7.0, `research_paper` 6.0), code (`github_star_project`/`shipped_product` 7.0, `github_early_builder` 5.0, `github_prolific` 4.0), education (`student_builder` 5.0), hackathon (`hackathon_win` 5.0, `hackathon_finalist` 3.0), fellowship/debate (`fellowship_finalist` 6.0, `debate_nationals` 4.0), connection (`connected_to_seeds` 3.0), and licensed enrichment for the discovery cohort only (`linkedin_created_recently` 8.0, `education_signal`/`job_change` 4.0, `web_presence` 3.0 for semantic web/Exa matches).
- `DEFAULT_WEIGHT = 3.0` — fallback weight for any signal type not listed in `WEIGHTS`.
- `DIVERSITY_BONUS_PER_CATEGORY = 0.15` — per-extra-category multiplier bonus used in the diversity term.
- `RECENCY_BONUS_PER_SIGNAL = 0.1` — additive raw-point-fraction bonus per recent signal.
- `RECENCY_BONUS_CAP = 5` — maximum number of signals that count toward the recency bonus.
- `weight_for(signal_type) -> float` — looks up a signal type's weight in `WEIGHTS`, falling back to `DEFAULT_WEIGHT`.
- `age_factor(graduation_year, as_of_year) -> float` — returns a multiplier favoring younger people: 1.4 if approximate age (`18 + as_of_year - graduation_year`) is under 18, 1.2 if under 20, otherwise 1.0 (or 1.0 if `graduation_year` is unknown).

## backend/scoring/backtest.py
Implements `BacktestRunner`, which validates the scoring formula against known founders and control (non-founder) people to measure recall and false-positive rate.

- `BacktestRunner` — runs the founder/control backtest and produces a report of flag accuracy, lead time, and top-contributing signal types.
  - `BacktestRunner.__init__(persons, signals, edges, engine, flag_threshold)` — stores repositories for persons/signals/graph edges, a `ScoringEngine`, and the score threshold above which a person is considered "flagged" as founder-like.
  - `BacktestRunner.run() -> dict` — for every known founder with a `breakout_date`, restricts signals and edges to those dated before the breakout, adds a `connected_to_seeds` signal derived from other founders, computes an adjusted score as of the breakout date, and does the same (using full history as of today) for controls; calibrates a reference scale from the median of the top-N founder adjusted scores (`reference.py`/`engine.reference_from`), normalizes all scores against it, and returns a dict with recall (`founders_flagged`/`founders_total`), average lead time in months before breakout, false-positive count/rate among controls, per-founder result rows, and the top signal types by total points contributed among flagged founders.
  - `BacktestRunner._first_crossing(detail, final_score) -> date | None` — finds the earliest date a founder's cumulative score (recomputed signal-by-signal and rescaled to the final normalization) would have crossed `flag_threshold`, used to compute lead-time-before-breakout.
  - `BacktestRunner._parse(iso) -> date` (static) — parses an ISO date string into a `date`.

## backend/scoring/reference.py
Provides the shared founder reference-scale computation used identically by both the backtest and live discovery ranking, so all scores in the product sit on one consistent absolute scale.

- `founder_prebreakout_adjusted(persons, signals, edges, engine) -> dict[str, float]` — for every known founder with a `breakout_date`, computes their adjusted score using only signals and graph edges dated before that breakout (including a derived `connected_to_seeds` signal against the other founders as seeds), returning a person-id-to-adjusted-score map.
- `founder_reference(persons, signals, edges, engine, top_n=10) -> float` — computes `founder_prebreakout_adjusted` and reduces it via `engine.reference_from` to a single robust reference value (median of the top-N founder scores) used to calibrate normalization everywhere.
