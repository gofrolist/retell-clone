-- Grafana's read-only role. Run as a superuser (the `postgres` user on Cloud
-- SQL), from infra/README.md § Grafana database access. Safe to re-run: that
-- is how the password is rotated and how a half-applied run is recovered.
--
-- The application deliberately cannot do this: creating roles and granting
-- privileges is exactly the authority a compromised API should not hold.
--
-- What this grants, and what it very deliberately does not: `grafana_ro` can
-- read the `metrics` views and the `pricing.model_price` view, and holds
-- no privilege on any base table. That is what makes call transcripts
-- unreachable rather than merely unselected -- `calls.transcript` and
-- `calls.transcript_object` hold customer conversation content, and a role
-- with SELECT on `calls` is one ad-hoc query away from reading all of it.
-- Views in Postgres 16 default to security_invoker = off, so they execute with
-- their owner's privileges: the view reaches the base table and the caller does
-- not. Leaving that default in place is load-bearing.

-- Re-runnable, on purpose. Under ON_ERROR_STOP a bare CREATE ROLE aborts the
-- whole script the second time it is run -- including the second time after a
-- *partial* first run, which is the easy way to get here: if the grants below
-- fail because the API has not yet booted with these schemas, the role already
-- exists and the retry dies on line one with the grants still unapplied. Same
-- trap on a GRAFANA_DB_PASSWORD rotation.
\set ON_ERROR_STOP on

-- The schemas the API will create at boot, created here first and owned by the
-- API's role so it can still install views into them. Without this the grants
-- below depend on the API having booted at least once with the metrics schema,
-- and the run order becomes something an operator has to get right.
CREATE SCHEMA IF NOT EXISTS metrics AUTHORIZATION arhiteq;
CREATE SCHEMA IF NOT EXISTS pricing AUTHORIZATION arhiteq;

-- psql does not interpolate :'pw' inside a dollar-quoted body, so the role is
-- created without a password here and given one by the ALTER below -- which is
-- also what makes a password rotation just another run of this script.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro;
  END IF;
END
$$;

-- The password comes from the environment so it never lands in this file or in
-- shell history:
--   psql -v pw="$GRAFANA_DB_PASSWORD" -f infra/sql/grafana_ro.sql
ALTER ROLE grafana_ro LOGIN PASSWORD :'pw';

GRANT CONNECT ON DATABASE arhiteq TO grafana_ro;
GRANT USAGE ON SCHEMA metrics, pricing TO grafana_ro;

-- The views that exist right now -- none, on a database whose API has not
-- booted yet, which ALL TABLES handles and a named `pricing.model_price`
-- would not. Same scope as the ALTER DEFAULT PRIVILEGES below, deliberately:
-- one statement for what exists, one for what the next boot creates.
GRANT SELECT ON ALL TABLES IN SCHEMA metrics TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA pricing TO grafana_ro;

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
