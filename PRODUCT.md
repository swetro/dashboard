# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Coached recreational runners training toward a specific race goal (5K through marathon).
The viewer is the athlete themselves, not a coach: they receive a personal link
(`?u=<id>`) and open their own dashboard directly, with no login or signup flow.

## Product Purpose

Give each athlete a personalized, plain-language read of their own training: an
AI-generated "PULSE" verdict/score for the week, injury-risk (ACWR) monitoring, weekly
training-load metrics, and a taper plan counting down to their race goal. Success means
the athlete can tell, at a glance, whether their training is on track and safe, and what
their next session should be.

## Positioning

Two combined mechanisms a neighboring activity tracker (Strava, Garmin Connect,
TrainingPeaks) doesn't offer together: (1) an AI-generated, plain-language "PULSE"
verdict/score judging the week's training, not just raw metrics; (2) it is a companion
layer, not a head-on competitor — it ingests training data produced elsewhere (a
partner app, per the transform pipeline's `--meta`/input format) and adds this
analysis and coach-reviewed judgment on top, rather than being the primary
activity-logging tool.

## Operating Context

- The data pipeline (`transformar_json.py`) is run by hand, separately from the app
  build: it converts a JSON export from a partner app's database into the format this
  dashboard reads (`public/data/<userId>.json`). It is not wired into `npm run build`,
  so app changes and data refreshes are independent and can silently drift apart.
- An optional `--con-pulse` pipeline step calls the Anthropic API to generate the PULSE
  narrative; it requires `ANTHROPIC_API_KEY` set by hand in the shell (no `.env` or
  secrets manager in this repo) and degrades silently (skips the "Pulse" block) if the
  key or `anthropic` package is missing.
- Deploy is fully manual (`npm run build && npm run deploy` via `gh-pages`); there is no
  CI.

## Capabilities and Constraints

- No authentication. Access is by unguessable token (`?u=<token>`, see
  `transformar_json.py`'s `obtener_token`) for the current pilot cohort, but older
  `public/data/*.json` files from before the token migration still sit under their
  sequential user ID and haven't all been moved over. This is an accepted MVP risk: the
  site is public but unindexed and undistributed, reachable only via a direct link sent
  to each athlete. Revisit before scaling to more users.
- Real production athlete data (training history, names) is committed to the repo and
  served statically and publicly by GitHub Pages — not fixtures, except `demo.json`.
- The `taper` field is always emitted empty (`[]`) by the current pipeline. The UI can
  render a taper plan, but no real data populates it yet — an absent taper section is
  expected, not a bug.
- `meta.metaCarrera.fecha === "2027-01-01"` is a sentinel meaning "no race goal set,"
  not a real date; the UI reads it as "hasMeta = false."
- An `isPro` flag exists on user records; its product meaning (feature gating, tier,
  or purely informational) is unconfirmed — do not assume it currently gates anything
  in this dashboard.
- This dashboard is currently the entire product surface, and is explicitly meant as
  the MVP for a new product direction that may eventually replace the existing partner
  platform entirely. Treat data/product decisions here as candidates for becoming the
  primary surface, not as a minor satellite report.

## Brand Commitments

Product name "Swetro"; logo asset at `public/swetro-logo.png`. Product copy is
Spanish-language, written directly to the athlete in second person (e.g., "TU PULSE DE
HOY").

## Evidence on Hand

- Real per-athlete JSON under `public/data/` (`2`, `9`, `24`, `8648`, `9860`, `30065`,
  `30560`, `33866`, `43718`), plus a `demo.json` fixture ("Jose Guillermo Calderón"
  training for "Maratón Seúl").
- The raw exports and the export pipeline that produces this data live in the sibling
  `swetro-export` project (SQL Server/Azure → JSON), outside this repo.
- No testimonials, case studies, press, or pricing exist yet — do not fabricate them.

## Product Principles

- The athlete's own numbers and the AI's verdict are the product; never invent
  benchmarks, testimonials, or pricing claims to fill a gap.
- Frame every metric through safety: injury-risk (ACWR) and training load exist to
  keep a runner training toward a real race goal without getting hurt.
- Zero-friction access: opening a personal link should be enough to understand "am I on
  track and safe" — no login, no setup.
- Design and data decisions should anticipate this MVP becoming the primary product
  surface, not stay scoped as a minor companion report.
- The data pipeline is manual and coach-mediated for now; don't assume real-time or
  self-serve data ingestion.
