-- Grafana's read-only role. Run once per database, as a superuser (the
-- `postgres` user on Cloud SQL), from infra/README.md § Grafana database access.
--
-- The application deliberately cannot do this: creating roles and granting
-- privileges is exactly the authority a compromised API should not hold.
--
-- What this grants, and what it very deliberately does not: `grafana_ro` can
-- read the four `metrics` views and the `pricing.model_price` view, and holds
-- no privilege on any base table. That is what makes call transcripts
-- unreachable rather than merely unselected -- `calls.transcript` and
-- `calls.transcript_object` hold customer conversation content, and a role
-- with SELECT on `calls` is one ad-hoc query away from reading all of it.
-- Views in Postgres 16 default to security_invoker = off, so they execute with
-- their owner's privileges: the view reaches the base table and the caller does
-- not. Leaving that default in place is load-bearing.

\set ON_ERROR_STOP on

-- The password comes from the environment so it never lands in this file or in
-- shell history:
--   psql -v pw="$GRAFANA_DB_PASSWORD" -f infra/sql/grafana_ro.sql
CREATE ROLE grafana_ro LOGIN PASSWORD :'pw';

GRANT CONNECT ON DATABASE arhiteq TO grafana_ro;
GRANT USAGE ON SCHEMA metrics, pricing TO grafana_ro;

-- The views that exist right now.
GRANT SELECT ON ALL TABLES IN SCHEMA metrics TO grafana_ro;
GRANT SELECT ON pricing.model_price TO grafana_ro;

-- ...and the ones the next API boot will replace them with. The API installs
-- these views by DROP-then-CREATE on every boot (services/view_ddl.py), which
-- takes each view's grants down with it. Without these two statements the
-- dashboard works until the next deploy and then breaks with no deploy in the
-- blast radius -- the failure lands hours later, on a Grafana nobody touched.
-- FOR ROLE arhiteq because default privileges apply to objects created by a
-- named role, and the API connects as `arhiteq`.
ALTER DEFAULT PRIVILEGES FOR ROLE arhiteq IN SCHEMA metrics
  GRANT SELECT ON TABLES TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE arhiteq IN SCHEMA pricing
  GRANT SELECT ON TABLES TO grafana_ro;

-- A runaway panel must not be able to sit on the production database.
ALTER ROLE grafana_ro SET statement_timeout = '30s';
