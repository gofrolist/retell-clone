# Automated Release Process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merging a release-please release PR creates a tag + GitHub release with a changelog, then GitHub Actions builds all three images at that version and deploys them to GKE via helm.

**Architecture:** Three new workflows (PR-title lint, release-please, deploy) plus a Terraform file granting GitHub Actions keyless GCP access via Workload Identity Federation. The deploy re-renders the helm chart with `--reuse-values` so secret values never enter CI. Spec: `docs/superpowers/specs/2026-07-12-release-process-design.md`.

**Tech Stack:** GitHub Actions, release-please v5, Google WIF (`google-github-actions/auth`), Artifact Registry, GKE + Helm, Terraform.

## Global Constraints

- All GitHub Actions MUST be SHA-pinned with a `# vX.Y.Z` comment (repo convention, see `.github/workflows/ci.yml`).
- `main` is protected; all changes land via PR. Squash-merge uses the PR title as the commit message.
- No secret values in the repo. `infra/private/` is gitignored and stays that way.
- Registry: `us-east1-docker.pkg.dev/usan-retirement/arhiteq`. GKE cluster `arhiteq`, region `us-east1`, project `usan-retirement`. Helm release `arhiteq` in namespace `arhiteq`, chart `infra/helm/arhiteq`.
- Version bootstrap: `0.1.7` (highest currently-deployed image tag), so the first release is `v0.2.0`.
- Work happens on the existing `feat/release-process` branch.

### Verified action pins (fetched 2026-07-12 from each repo's latest release)

| Action | SHA | Version |
|---|---|---|
| `actions/checkout` | `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0` | v7.0.0 |
| `amannn/action-semantic-pull-request` | `48f256284bd46cdaab1048c3721360e808335d50` | v6.1.1 |
| `googleapis/release-please-action` | `45996ed1f6d02564a971a2fa1b5860e934307cf7` | v5.0.0 |
| `google-github-actions/auth` | `7c6bc770dae815cd3e89ee6cdf493a5fab2cc093` | v3 |
| `google-github-actions/setup-gcloud` | `aa5489c8933f4cc7a4f7d45035b3b1440c9c10db` | v3.0.1 |
| `google-github-actions/get-gke-credentials` | `3da1e46a907576cefaa90c484278bb5b259dd395` | v3.0.0 |
| `azure/setup-helm` | `9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310` | v5.0.1 |

---

### Task 1: PR-title lint workflow

**Files:**
- Create: `.github/workflows/pr-title.yml`

**Interfaces:**
- Produces: a status check context named `pr-title` (the job name) that Task 6 adds to `main`'s required checks. Task 2's release PRs (titled `chore(main): release X.Y.Z`) must pass it — `chore` is in the action's default allowed types, so they do.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/pr-title.yml
name: PR title

on:
  pull_request:
    types: [opened, edited, synchronize]

permissions:
  pull-requests: read

jobs:
  pr-title:
    runs-on: ubuntu-latest
    steps:
      - uses: amannn/action-semantic-pull-request@48f256284bd46cdaab1048c3721360e808335d50 # v6.1.1
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

(No `with:` block — the action's default allowed types are the conventional-commit set: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.)

- [ ] **Step 2: Validate YAML syntax**

Run: `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/pr-title.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/pr-title.yml
git commit -m "ci: enforce conventional-commit PR titles"
```

---

### Task 2: release-please config + workflow

**Files:**
- Create: `release-please-config.json`
- Create: `.release-please-manifest.json`
- Create: `.github/workflows/release-please.yml`

**Interfaces:**
- Consumes: repo secret `RELEASE_PLEASE_TOKEN` (fine-grained PAT, created in Task 6 — the workflow merges fine before the secret exists; it just fails until then).
- Produces: on merge of the release PR, a git tag `vX.Y.Z` and a GitHub release whose `published` event triggers Task 4's deploy workflow. Maintains `CHANGELOG.md` and `version.txt` at repo root.

- [ ] **Step 1: Write the config files**

```json
// release-please-config.json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "packages": {
    ".": {
      "release-type": "simple",
      "include-component-in-tag": false
    }
  }
}
```

```json
// .release-please-manifest.json
{
  ".": "0.1.7"
}
```

- [ ] **Step 2: Write the workflow**

```yaml
# .github/workflows/release-please.yml
name: release-please

on:
  push:
    branches: [main]

# The fine-grained PAT (not GITHUB_TOKEN) is deliberate: PRs and releases
# created with GITHUB_TOKEN don't trigger other workflows, so the release PR
# would never run CI (required by branch protection) and the release would
# never trigger the deploy.
permissions: {}

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7 # v5.0.0
        with:
          token: ${{ secrets.RELEASE_PLEASE_TOKEN }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

- [ ] **Step 3: Validate YAML + JSON syntax**

Run:
```bash
uv run --with pyyaml python -c "import yaml, json; yaml.safe_load(open('.github/workflows/release-please.yml')); json.load(open('release-please-config.json')); json.load(open('.release-please-manifest.json')); print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add release-please-config.json .release-please-manifest.json .github/workflows/release-please.yml
git commit -m "ci: release-please versioning and changelog"
```

---

### Task 3: Terraform — WIF pool/provider + deployer service account

**Files:**
- Create: `infra/terraform/github-deploy.tf`

**Interfaces:**
- Produces: terraform outputs `deploy_workload_identity_provider` and `deploy_service_account`, which Task 6 copies into repo variables `GCP_WIF_PROVIDER` and `GCP_DEPLOYER_SA` consumed by Task 4's workflow.

- [ ] **Step 1: Write the Terraform file**

```hcl
# infra/terraform/github-deploy.tf
# Keyless GitHub Actions → GCP auth for the release deploy workflow
# (.github/workflows/deploy.yml). A Workload Identity Pool trusts GitHub's
# OIDC issuer, locked to this repository; the deployer SA can push images
# and drive helm against the cluster, and nothing else.

locals {
  github_repository = "gofrolist/retell-clone"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == \"${local.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "arhiteq-deployer"
  display_name = "GitHub Actions release deployer"
}

resource "google_project_iam_member" "deployer_artifact_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_gke" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${local.github_repository}"
}

output "deploy_workload_identity_provider" {
  description = "Set as the GCP_WIF_PROVIDER repo variable."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_service_account" {
  description = "Set as the GCP_DEPLOYER_SA repo variable."
  value       = google_service_account.deployer.email
}
```

- [ ] **Step 2: Format + validate**

Run: `terraform -chdir=infra/terraform fmt && terraform -chdir=infra/terraform validate`
Expected: `Success! The configuration is valid.` (the local dir is already initialized; if validate complains about init, run `terraform -chdir=infra/terraform init -backend=false` first)

- [ ] **Step 3: Commit**

```bash
git add infra/terraform/github-deploy.tf
git commit -m "feat(infra): WIF + deployer SA for GitHub Actions deploys"
```

(`terraform apply` happens in Task 6 — it needs your gcloud credentials.)

---

### Task 4: Deploy workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: repo variables `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`, `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (set in Task 6); the `release: published` event from Task 2; terraform resources from Task 3.
- Produces: prod running the released version.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      version:
        description: "Existing release tag to deploy (e.g. v0.2.0) — redeploy or rollback"
        required: true
        type: string

permissions:
  contents: read
  id-token: write # OIDC token for google-github-actions/auth

concurrency:
  group: deploy-prod
  cancel-in-progress: false

env:
  REGISTRY: us-east1-docker.pkg.dev/usan-retirement/arhiteq
  GKE_CLUSTER: arhiteq
  GKE_LOCATION: us-east1
  GCP_PROJECT: usan-retirement
  # Baked into the public JS bundle at build time — not secrets.
  NEXT_PUBLIC_API_URL: https://api.usanretirement.com

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Resolve version
        id: v
        run: echo "version=${{ github.event.release.tag_name || inputs.version }}" >> "$GITHUB_OUTPUT"

      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          ref: ${{ steps.v.outputs.version }}

      - uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}

      - uses: google-github-actions/setup-gcloud@aa5489c8933f4cc7a4f7d45035b3b1440c9c10db # v3.0.1
        with:
          install_components: gke-gcloud-auth-plugin

      - name: Configure docker for Artifact Registry
        run: gcloud auth configure-docker us-east1-docker.pkg.dev --quiet

      - name: Build images
        run: |
          V="${{ steps.v.outputs.version }}"
          docker build -t "$REGISTRY/arhiteq-api:$V" backend/
          docker build -t "$REGISTRY/arhiteq-worker:$V" worker/
          docker build -t "$REGISTRY/arhiteq-dashboard:$V" \
            --build-arg NEXT_PUBLIC_API_URL="$NEXT_PUBLIC_API_URL" \
            --build-arg NEXT_PUBLIC_GOOGLE_CLIENT_ID="${{ vars.NEXT_PUBLIC_GOOGLE_CLIENT_ID }}" \
            frontend/

      - name: Push images
        run: |
          V="${{ steps.v.outputs.version }}"
          docker push "$REGISTRY/arhiteq-api:$V"
          docker push "$REGISTRY/arhiteq-worker:$V"
          docker push "$REGISTRY/arhiteq-dashboard:$V"

      - uses: google-github-actions/get-gke-credentials@3da1e46a907576cefaa90c484278bb5b259dd395 # v3.0.0
        with:
          cluster_name: ${{ env.GKE_CLUSTER }}
          location: ${{ env.GKE_LOCATION }}
          project_id: ${{ env.GCP_PROJECT }}

      - uses: azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310 # v5.0.1

      - name: Helm upgrade
        # --reuse-values: re-render the (possibly updated) chart with the
        # values already live in the cluster, so secret values from
        # infra/private/ never enter CI. A chart change that ADDS a required
        # secret value needs one local `helm upgrade -f
        # infra/private/arhiteq-prod.yaml --reuse-values` first (see infra/README.md).
        run: |
          V="${{ steps.v.outputs.version }}"
          helm upgrade arhiteq infra/helm/arhiteq -n arhiteq \
            --reuse-values \
            --set "api.image.tag=$V,worker.image.tag=$V,dashboard.image.tag=$V" \
            --atomic --wait --timeout 10m
```

- [ ] **Step 2: Validate YAML syntax**

Run: `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: build, push, and helm-deploy on release publish"
```

---

### Task 5: Docs — releasing runbook + CLAUDE.md

**Files:**
- Modify: `infra/README.md` (sections 6–7, the manual build/push + deploy instructions)
- Modify: `CLAUDE.md` (the `## Commands` section)

**Interfaces:**
- Consumes: the flow implemented in Tasks 1–4 (documentation must match workflow/file names exactly: `pr-title.yml`, `release-please.yml`, `deploy.yml`, `RELEASE_PLEASE_TOKEN`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`, `NEXT_PUBLIC_GOOGLE_CLIENT_ID`).

- [ ] **Step 1: Add a "Releasing" section to `infra/README.md`**

Add the new section directly ABOVE the current "## 6. Build & push images" header, and reframe the manual path by appending " (manual / break-glass)" to both the "## 6. Build & push images" and "## 7. Deploy Arhiteq" headers. New section content:

```markdown
## Releasing (normal path)

Releases are automated — never bump image tags by hand:

1. Merge PRs to `main` with conventional-commit titles (`feat: …`, `fix: …`;
   enforced by the `pr-title` check).
2. release-please maintains a release PR that accumulates `CHANGELOG.md`.
   Merging it tags `vX.Y.Z` and publishes a GitHub release.
3. The release triggers `.github/workflows/deploy.yml`: builds + pushes all
   three images at `vX.Y.Z`, then
   `helm upgrade arhiteq --reuse-values --set …image.tag=vX.Y.Z --atomic`.

Redeploy/rollback: run the Deploy workflow manually (workflow_dispatch) with
any existing release tag.

Caveat: `--reuse-values` re-renders the chart with the values already in the
cluster. A chart change that introduces a NEW required value (e.g. a new
secret) must first be applied locally once:
`helm upgrade arhiteq infra/helm/arhiteq -n arhiteq -f infra/private/arhiteq-prod.yaml --reuse-values`.

One-time setup (already done, recorded for rebuild-from-scratch):

- `terraform apply` creates the WIF pool/provider + `arhiteq-deployer` SA
  (`infra/terraform/github-deploy.tf`).
- Repo variables: `GCP_WIF_PROVIDER` / `GCP_DEPLOYER_SA` (from the terraform
  outputs `deploy_workload_identity_provider` / `deploy_service_account`) and
  `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (OAuth client id, public by design).
- Repo secret `RELEASE_PLEASE_TOKEN`: fine-grained PAT scoped to this repo,
  permissions Contents: read/write + Pull requests: read/write. Needed
  because events created with the default `GITHUB_TOKEN` don't trigger
  workflows (CI on the release PR, deploy on release publish).
- `pr-title` added to the required status checks on `main`.
```

- [ ] **Step 2: Add the release rules to `CLAUDE.md`**

In the `## Commands` section of `CLAUDE.md`, append one bullet:

```markdown
- Releases: PR titles must be conventional commits (`pr-title` check);
  merging release-please's release PR tags + deploys everything; never bump
  image tags by hand (see `infra/README.md` § Releasing)
```

- [ ] **Step 3: Commit**

```bash
git add infra/README.md CLAUDE.md
git commit -m "docs: releasing runbook and conventional-commit rule"
```

---

### Task 6: One-time setup, PR, and live validation

**Files:** none (operations only). Requires the user's credentials (github.com PAT creation, gcloud) — run interactively with the user, not in a subagent.

**Interfaces:**
- Consumes: everything from Tasks 1–5.

- [ ] **Step 1: terraform apply**

Run: `terraform -chdir=infra/terraform apply`
Expected plan: 6 new resources (`google_iam_workload_identity_pool.github`, `…pool_provider.github`, `google_service_account.deployer`, two `google_project_iam_member`, one `google_service_account_iam_member`), 0 changed, 0 destroyed. Review, answer `yes`.

- [ ] **Step 2: Set repo variables from terraform outputs**

```bash
gh variable set GCP_WIF_PROVIDER --body "$(terraform -chdir=infra/terraform output -raw deploy_workload_identity_provider)"
gh variable set GCP_DEPLOYER_SA --body "$(terraform -chdir=infra/terraform output -raw deploy_service_account)"
# OAuth client id (public, baked into the JS bundle) — grab from the private helm values:
gh variable set NEXT_PUBLIC_GOOGLE_CLIENT_ID --body "$(grep googleOauthClientId infra/private/arhiteq-prod.yaml | awk -F'"' '{print $2}')"
gh variable list   # verify all three
```

- [ ] **Step 3: User creates the release-please PAT (manual, browser)**

Ask the user to: GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token; Resource owner `gofrolist`; Only select repositories → `retell-clone`; Repository permissions: **Contents: Read and write**, **Pull requests: Read and write**; expiration per their preference (calendar reminder to rotate). Then:

```bash
gh secret set RELEASE_PLEASE_TOKEN   # paste the token when prompted
```

- [ ] **Step 4: Push branch and open the PR (conventional title!)**

```bash
git push -u origin feat/release-process
gh pr create --title "feat: automated release process (release-please + WIF deploy)" --body "$(cat <<'EOF'
Implements docs/superpowers/specs/2026-07-12-release-process-design.md:
PR-title lint, release-please changelog/tag/release, and a release-triggered
build+push+helm deploy via Workload Identity Federation.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: CI (backend/worker/frontend/infra) green, plus the new `PR title / pr-title` check green.

- [ ] **Step 5: Add `pr-title` to required status checks**

(After the check has reported on the PR at least once so GitHub knows the context.)

```bash
gh api -X POST repos/gofrolist/retell-clone/branches/main/protection/required_status_checks/contexts -f "contexts[]=pr-title"
gh api repos/gofrolist/retell-clone/branches/main/protection/required_status_checks --jq .contexts
```

Expected: `["backend","worker","frontend","infra","pr-title"]`

- [ ] **Step 6: Merge the PR, watch release-please**

Merge (squash) the PR. The `release-please` workflow runs on the push to `main` and opens a PR titled `chore(main): release 0.2.0` containing `CHANGELOG.md`, `version.txt`, and a manifest bump. Verify its CI runs (proves the PAT works — with `GITHUB_TOKEN` no checks would appear).

- [ ] **Step 7: Merge the release PR, watch the deploy**

Merging it makes release-please create tag `v0.2.0` + the GitHub release; the Deploy workflow fires. Watch: `gh run watch`. Expected: build + push + `helm upgrade` succeed (`--atomic` guards failure).

- [ ] **Step 8: Verify prod**

```bash
kubectl -n arhiteq get deploy -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
```

Expected: all three images at `:v0.2.0`. Then open `https://dashboard.usanretirement.com`, check the agent LLM dropdown shows the Gemini-only list (this release ships the pending fix from PR #56).

- [ ] **Step 9: Rollback-path smoke test (optional but recommended)**

Run the Deploy workflow manually with `version: v0.2.0` (same tag — a no-op redeploy) to prove the workflow_dispatch path works before you ever need it in anger:

```bash
gh workflow run deploy.yml -f version=v0.2.0 && gh run watch
```
