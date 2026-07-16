# Release process — design

Date: 2026-07-12
Status: approved (design review in conversation)

## Problem

Deploys are fully manual (local `docker build/push` + `helm upgrade` with
`infra/private/` values) and nothing ties "code merged" to "code running in
prod" — the dashboard served a weeks-old model list because its image was
never rebuilt after the fix merged. Image tags are bumped by hand and have
drifted per component (api v0.1.5, dashboard v0.1.6, worker v0.1.7). There
are no git tags, no changelog, and no record of what any deployed version
contains.

## Decisions (made in design review)

- **One platform version.** A release tags the repo once (e.g. `v0.2.0`) and
  builds/deploys all three images at that tag, changed or not.
- **Fully automated in CI.** Publishing a release builds, pushes, and deploys
  from GitHub Actions. No human step after merging the release PR.
- **release-please** drives versioning + changelog from conventional-commit
  PR titles. `CHANGELOG.md` lives in the repo.
- **No approval gate** on the deploy; merging the release PR is the approval.

## Release flow

1. Normal PRs merge to `main` with conventional-commit titles
   (`feat: …` / `fix: …` / `chore: …`). Squash-merge uses the PR title as the
   commit message; a PR-title lint check blocks non-conforming titles.
2. On each push to `main`, release-please opens/updates a single release PR
   that accumulates `CHANGELOG.md` and bumps the version manifest
   (`feat:` → minor, `fix:` → patch, pre-1.0 semantics).
3. Merging the release PR makes release-please create the git tag `vX.Y.Z`
   and the GitHub release with the changelog section as its notes.
4. `release: published` triggers the deploy workflow: GCP auth via Workload
   Identity Federation → build + push `arhiteq-api`, `arhiteq-worker`,
   `arhiteq-dashboard` tagged `vX.Y.Z` → `helm upgrade` on GKE.

Version bootstrap: `.release-please-manifest.json` starts at `0.1.7` (highest
tag currently deployed), so the first release is `v0.2.0`.

## Components

### `.github/workflows/pr-title.yml`

Validates the PR title is a conventional commit
(`amannn/action-semantic-pull-request`, SHA-pinned like all actions in this
repo). Runs on `pull_request` types `opened/edited/synchronize`. Added to the
required checks on `main`.

### `.github/workflows/release-please.yml`

`googleapis/release-please-action` on push to `main`, `release-type: simple`
(maintains `CHANGELOG.md` + `version.txt`), config + manifest files in repo
root.

**Token gotcha:** PRs created with the default `GITHUB_TOKEN` do not trigger
CI, and protected `main` requires passing checks, so the release PR could
never merge. Fix: a fine-grained PAT (this repo only; permissions:
contents read/write, pull requests read/write) stored as the
`RELEASE_PLEASE_TOKEN` repo secret and passed to the action. Creating the PAT
is a one-time manual step (documented in the runbook).

### `.github/workflows/deploy.yml`

Triggers:
- `release: published` — deploys the just-released tag.
- `workflow_dispatch` with a `version` input — redeploy or roll back to any
  existing tag.

Steps:
1. `google-github-actions/auth` with Workload Identity Federation (no stored
   keys), then `google-github-actions/get-gke-credentials`.
2. Build + push the three images tagged with the version. The dashboard build
   passes `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_GOOGLE_CLIENT_ID` build args;
   these are baked into the public JS bundle, not secrets, and live as
   workflow `env` alongside registry/cluster coordinates.
3. `helm upgrade arhiteq infra/helm/arhiteq -n arhiteq --reuse-values
   --set api.image.tag=vX.Y.Z,worker.image.tag=vX.Y.Z,dashboard.image.tag=vX.Y.Z
   --atomic --wait --timeout 10m`.

`--reuse-values` re-renders the chart with the values already live in the
cluster (originally from `infra/private/arhiteq-prod.yaml`), so CI never
sees secret values. `--atomic` rolls back automatically if the rollout fails.
A concurrency group (`deploy-prod`, no cancel-in-progress) serializes deploys.

**Known limitation:** a chart change that introduces a *new required secret
value* needs one local `helm upgrade -f infra/private/arhiteq-prod.yaml
--reuse-values` to seed it before (or instead of) the CI deploy. Routine
releases never need this. Documented in the runbook.

### `infra/terraform/github-deploy.tf`

- Workload Identity Pool + GitHub OIDC provider, attribute condition locked
  to this repository.
- `arhiteq-deployer` service account with `roles/artifactregistry.writer`
  and `roles/container.developer`, impersonable only by this repo's
  workflows via the WIF provider.
- Outputs for the provider resource name + SA email (referenced by
  `deploy.yml` env).

### Docs

- `infra/README.md`: new "Releasing" section (normal path = merge release PR;
  manual build/push/deploy kept as the seed-new-secret / break-glass path);
  one-time setup notes (PAT, terraform apply, required checks).
- `CLAUDE.md`: one line — PR titles must be conventional commits.

## Error handling

- Bad PR title → lint check fails, PR blocked before merge.
- Failed image build/push → workflow red before any cluster change.
- Failed rollout → `--atomic` rolls the release back; workflow red.
- Bad release shipped → re-run `deploy.yml` via `workflow_dispatch` with the
  previous version tag.
- release-please PR CI failure → fix `main` with a normal PR; the release PR
  rebases itself.

## Testing / validation

- `helm lint` and terraform validate already run in CI and cover the chart +
  new TF file syntax.
- The pipeline is validated live: merge the setup PR → release-please opens
  the release PR → merge it → watch `v0.2.0` build and roll out (this also
  ships the pending Gemini-dropdown fix to prod).
- `workflow_dispatch` redeploy of `v0.2.0` exercises the rollback path.

## Out of scope

- Per-component versioning/releases.
- Staging environment or approval gates.
- Bumping `pyproject.toml` / `package.json` versions (can be added later via
  release-please `extra-files`).

## One-time manual steps (user)

1. Create the fine-grained PAT and add it as the `RELEASE_PLEASE_TOKEN`
   repo secret.
2. `terraform apply` for the WIF + deployer SA resources.
3. Add the PR-title check to `main` required status checks.
