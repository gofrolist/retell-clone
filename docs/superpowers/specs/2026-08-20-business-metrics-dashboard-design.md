# Business metrics dashboard — design

Operator dashboard in Grafana answering three questions: how much is the
platform being used, how hard is it being pushed, and what does serving it
cost per account.

## Why this exists

The Grafana stack measures the *system* — request rates, latency, pod health.
Nothing measures the *business*: how many accounts exist, how many agents they
built, how much they talk, and what that consumes. Those questions are
currently answered by hand with SQL, which means they are not answered.

## The state this is designed against

Measured in prod on 2026-08-20, and it matters more than it looks:

| thing | count |
| --- | --- |
| workspaces | 1 |
| workspace members | 2 |
| agents | 7 |
| calls | 228 (145 with `duration_ms > 0`) |
| calls carrying `latency` | 45 |
| total talk time | 109.7 minutes |

**There is one account.** The per-workspace split is built in from day one
because grouping by `workspace_id` costs nothing now and a retrofit costs a
rewrite — but nobody should expect the tenant comparison to say anything until
there are tenants. The numbers that inform decisions today are load, adoption,
and cost per minute.

**LLM turn counts start partway through history.** `latency` is null on 183 of
228 calls: realtime sessions recorded no latency samples at all until #261
fixed it. Turn-based panels therefore describe a suffix of history, and say so
on their face.

## Non-goals

- **Billing.** There is no revenue: no rate card per customer, no invoices, no
  subscription. `api/concurrency.py` states it outright. This dashboard reports
  cost-to-serve; it does not bill anyone.
- **Profit as a rendered number.** Pre-revenue, every workspace shows a loss,
  and a red negative margin column teaches nothing. The decision-useful figure
  is cost per call-minute and the break-even price it implies.
- **Metering real consumption.** Actual audio seconds, output tokens and TTS
  characters are not captured anywhere and are not added here. See
  "Where this goes next".
- **Customer-facing usage.** Operator-only. Nothing lands in the Next.js
  dashboard.

## Architecture

Grafana gains a second datasource pointing at Cloud SQL
(`10.145.0.2:5432/arhiteq`, Postgres 16). Panels are SQL. There is no
exporter, no proxy and no pipeline: the Grafana pod already sits in the VPC
that reaches the private IP, and the cluster has no NetworkPolicies to
traverse.

At 228 rows, analytical SQL against the OLTP database is free. The queries are
written to group and aggregate in the view layer so that moving to a
materialised rollup later is a change of which relation the panel reads, not a
rewrite of the panel.

### Alternatives rejected

**Prometheus with per-workspace labels.** Entity counts ("how many agents
exist") are database facts, not time series; retention is 15 days, so monthly
trends are impossible; and a `workspace_id` label multiplies cardinality by the
customer count permanently.

**Nightly rollup table.** Correct at volume, premature at 228 rows: it adds a
scheduled job to deploy and monitor, and makes every number a day stale. The
view layer is designed so this becomes a drop-in replacement when the calls
table makes panels slow.

## Security model

`grafana_ro` is a dedicated Postgres role, not the application's user. It gets
`CONNECT` on the database, `USAGE` on schema `metrics`, and `SELECT` on the
views in that schema. **It holds no grant on any base table.**

This is what makes transcripts unreachable rather than merely unselected.
`calls.transcript` and `calls.transcript_object` hold customer conversation
content; a role with `SELECT` on `calls` could read all of it, and a dashboard
that never selects a column is one ad-hoc query away from doing so. A view in
Postgres 16 defaults to `security_invoker = off`, so it executes with its
owner's privileges — the view reaches the base table and the caller does not.
Leaving that default in place is load-bearing; setting `security_invoker = on`
would break the grant model.

The role also carries `ALTER ROLE grafana_ro SET statement_timeout = '30s'`, so
a runaway panel cannot sit on the production database.

Its password follows the path the OAuth client already established: into
`infra/private/prod.env`, applied as a k8s Secret by `gen-values.sh`, injected
into the datasource as `secureJsonData`, and never written into
`monitoring-values.yaml` (which Helm stores in the release, where `helm get
values` would print it). The password is folded into the existing checksum pod
annotation, so rotating it actually rolls Grafana instead of leaving the old
credential live in a running process.

Creating the role and granting it is a one-time operator step — it needs
privileges the application deliberately does not have — documented in
`infra/README.md` next to the OAuth client setup.

## Schema

### DDL delivery

This repo has no Alembic. Schema is `Base.metadata.create_all` plus idempotent
statements applied at API boot (`main.py:_apply_column_backfills`,
`_BACKFILL_INDEXES`). The views follow that pattern exactly: a `_METRICS_VIEWS`
tuple of `CREATE SCHEMA IF NOT EXISTS metrics` and `CREATE OR REPLACE VIEW`
statements executed on boot. Idempotent, ships with the release, introduces no
new deploy mechanism.

`CREATE OR REPLACE VIEW` cannot change a column's type or drop a column. Any
view change that is not purely additive ships as `DROP VIEW IF EXISTS` followed
by `CREATE VIEW`, in that order, in the same tuple.

### `cost_rates`

A real SQLAlchemy model, so `create_all` builds it:

| column | type | note |
| --- | --- | --- |
| `id` | int pk | |
| `component` | str(64) | `gemini_live_audio`, `telnyx_inbound`, `telnyx_outbound`, `infra_fixed_monthly` |
| `unit` | str(16) | `per_minute` or `per_month` |
| `unit_price_usd` | Numeric(12, 6) | |
| `effective_from_ms` | bigint | |
| `note` | str(255) | where the figure came from |

Effective-dated on purpose. When a provider changes its price you insert a new
row; a call is costed with the rate in force at its `start_timestamp`, so last
quarter's costs keep the price that was actually paid instead of being
silently restated by today's number.

`Numeric`, not float: these values are summed across thousands of rows and
compared against provider invoices.

**Rates are operator-entered, not seeded with guesses.** A wrong hardcoded
price is worse than a missing one, because it produces a confident number. The
table ships empty; `infra/README.md` documents where each figure comes from
(Vertex AI pricing for Gemini Live audio, the Telnyx portal for per-minute
voice, the GCP billing console for the fixed infra line). Until a component has
a rate, the economics panels report it as unknown — see the NULL rule below.

### Views

All four live in schema `metrics`.

**`workspace_daily`** — one row per workspace per day: calls, connected calls,
minutes, inbound/outbound split, failures, and LLM turns from
`sum((latency->'e2e'->>'num')::int)`.

**`call_cost`** — one row per call: minutes, the `cost_rates` row in force at
`start_timestamp`, and the estimated cost.

**`tenancy`** — current counts and creation timestamps for workspaces,
members, agents and phone numbers, so adoption can be drawn over time rather
than only as a total.

**`concurrency_hourly`** — peak overlapping calls per hour, derived from
`start_timestamp`/`end_timestamp`. This is the load answer Prometheus
structurally cannot give: it keeps 15 days, and the calls table keeps
everything.

## Cost model

Per call: `minutes × (gemini_live_audio + telnyx_<direction>)`, priced at the
rates in force when the call started.

The telephony leg is charged on `call_type = 'phone_call'` only. A web call
carries no Telnyx cost — it never touches the trunk — and pricing one as though
it did would overstate the cost of exactly the calls that are cheapest to
serve.

Fixed infrastructure — GKE, Cloud SQL, Redis, the LiveKit deployment — is a
monthly figure allocated across workspaces by their share of minutes, and
presented as **its own column**, never folded into the per-call number. Mixing
a fixed cost into a variable one produces a per-minute figure that falls as
volume rises and looks like an efficiency gain that is really just division.

## Panels

Four rows, each panel titled with the question it answers:

1. **Adoption** — workspaces, members, agents, phone numbers as stat tiles with
   period-over-period deltas.
2. **Load** — calls/day (stacked inbound/outbound), minutes/day, peak
   concurrency, failure ratio.
3. **Model usage** — turns/day, turns per call, average call duration.
4. **Economics** — cost/day, **cost per minute** as the headline stat, a
   per-workspace table (calls, minutes, variable cost, allocated infra,
   cost/min), and break-even price at a target-margin dashboard variable.

Panels are exported without an `__inputs` block and with the datasource uid
pinned, for the reason recorded in `infra/README.md`: file provisioning never
resolves `${DS_*}` placeholders, and every panel silently reads "Datasource not
found".

## Three honesty rules

Two people read this dashboard, and neither of them will have the schema in
their head.

1. **Zero-duration calls are shown, not dropped.** 83 of 228 calls never
   connected. They are excluded from minutes — a failed dial consumed no audio
   — and counted in their own panel, so the gap between "calls" and "minutes"
   is explained on screen rather than looking like a bug.
2. **A missing rate yields NULL, never 0.** An unpriced component must read as
   *unknown*, because a zero silently claims the component is free and makes
   the total look complete.
3. **Turn panels state their coverage.** Each carries `N of M calls` in the
   subtitle, since turn data begins at #261 rather than at the start of
   history.

## Verification

**Unit.** The views get pytest coverage against the existing test database with
seeded fixtures, asserting: zero-duration calls contribute no minutes; a call
whose component has no rate row produces NULL cost rather than 0; a call is
priced with the rate in force at its start rather than the newest rate; and
`concurrency_hourly` counts an overlapping pair as 2 and an adjacent pair as 1.

**Grants.** A test asserts the isolation actually holds — connect as
`grafana_ro`, `SELECT` from `metrics.workspace_daily` succeeds, `SELECT` from
`public.calls` raises insufficient privilege. Without this the security model
is an intention rather than a property.

**Prod.** Each panel query is run against the production database directly and
its output compared with the panel, the same way the Prometheus panels were
validated.

## Rollout

1. Merge the API change (model, `_METRICS_VIEWS`); boot creates schema, views
   and `cost_rates`.
2. Operator: create the `grafana_ro` role, grant it, set its `statement_timeout`.
3. Operator: add `GRAFANA_DB_PASSWORD` to `prod.env`; add the business partner
   to `GRAFANA_ALLOWED_EMAILS`.
4. Operator: insert the `cost_rates` rows.
5. Run `gen-values.sh` — datasource, dashboard and Secret land together.

Steps 2–4 are manual by design: each needs either a privilege the platform
should not hold or a figure only a human can supply.

## Where this goes next

**Metering.** The cost figure is `duration × rate`, which is wrong for any call
that burns unusual tokens — a long prompt, heavy tool use, a barge-in-heavy
conversation. Replacing it means recording real consumption per call (audio
seconds in/out, total tokens, tool invocations) in the worker and persisting it
to the `calls.call_cost` column that exists and has never been written. That
touches the worker hot path and the finalize contract, so it is its own spec.
`metrics.call_cost` is the seam: metered actuals replace estimates inside the
view, and no panel changes.

**Rollups.** When the calls table makes panels slow, `workspace_daily` becomes
a materialised table refreshed on a schedule. Panels already read the view
name.

**Revenue.** If pricing ever exists, a `workspace_price` table joined into
`call_cost` turns the break-even panel into a real margin panel.
