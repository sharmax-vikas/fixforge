# FixForge

FixForge is an approval-first AI GitHub Issue Resolution Agent. It takes a public GitHub issue URL, gathers evidence from a shallow clone, asks Codex for conservative patch candidates, evaluates each candidate in an isolated Git worktree, ranks the results, and can create a draft pull request only after final approval.

## Workflow

1. Submit a GitHub issue URL.
2. FixForge fetches the issue, clones (or reuses) the configured fork, maps issue terminology to source evidence, and presents an initial confidence assessment.
3. Approve Codex candidate generation.
4. Review candidate unified diffs and approve isolated patch application and tests.
5. Compare test result, modified-file count, and confidence score. Select the best candidate.
6. Approve the draft PR. FixForge creates a branch only in your fork, pushes it, and opens a draft PR to the upstream repository.

Every state-changing operation is a separate approval action. Analysis workspaces, candidate worktrees, and test output live under `.fixforge/` and are ignored by Git.

## Local setup

Python 3.11+ and Git are required.

```bash
cd /mnt/d/App/fixly
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn fixforge.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`. Interactive API documentation is available at `http://localhost:8000/docs`.

`pytest` is included in FixForge's runtime requirements so focused Python test commands can start. Some repositories additionally require their own extras (for example, `pip install -e ".[torch,testing]"` for a Transformers model test); FixForge preserves that dependency error in the candidate result instead of treating it as a code-test failure.

## Environment configuration

```dotenv
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-codex
GITHUB_TOKEN=...                       # needed for private repos and draft PR creation
FIXFORGE_FORK_REPOSITORY=owner/repo    # your fork of the issue repository
TEST_TIMEOUT_SECONDS=600
MAX_CANDIDATES=3
```

`FIXFORGE_FORK_REPOSITORY` is optional for evidence-only analysis. It is required to publish a fix safely: FixForge checks that it is a fork of the issue repository, then pushes only to that fork.

The generated test command is run inside each candidate's isolated worktree. Install repository-specific test dependencies in the host/container image when necessary; test command failures are retained in the candidate comparison rather than being treated as a passing result.

## Docker

```bash
docker compose up --build
```

The Compose volume persists cloned repositories and isolated candidate workspaces across container restarts. For a private repository or PR creation, pass a GitHub token via `.env`.

## REST API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/jobs` | Start an investigation with `{ "issue_url": "..." }` |
| `GET` | `/api/jobs/{job_id}` | Poll job status, evidence, candidates, logs, and metrics |
| `POST` | `/api/jobs/{job_id}/approvals` | Approve `generate_patches`, `evaluate_patches`, `select_best_patch`, or `create_draft_pr` |
| `GET` | `/health` | Readiness check |

For `select_best_patch`, include an optional `candidate_id`; otherwise FixForge selects the highest-ranked non-rejected candidate.

## Design notes

- GitHub API access uses a token only when configured; public issue reads work without one until GitHub rate limits apply.
- Codex must return JSON with unified diffs. Invalid diffs are rejected by `git apply --check` before tests run.
- Candidate scoring is intentionally transparent: test pass status is weighted most heavily, followed by Codex confidence and fewer modified files.
- The in-memory job store suits a local single-instance deployment. Replace it with Postgres/Redis and a queue worker before a multi-user production deployment.
