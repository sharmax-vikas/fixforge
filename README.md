# FixForge

**Approval-first AI GitHub issue resolution.**

FixForge turns a well-scoped GitHub issue into an evidence-backed, reviewable fix. It reads the issue, clones your fork, searches the checked-out source, generates conservative Codex patch candidates, evaluates each candidate in an isolated Git worktree, and opens a draft pull request only after every required approval.

> FixForge never pushes to an upstream repository. A final push is allowed only to the fork configured by you.

## What it does

| Stage | FixForge action | Your control |
| --- | --- | --- |
| 1. Investigate | Fetches the issue, clones/reuses your fork, and gathers source evidence | Review evidence |
| 2. Generate | Uses Codex to propose valid unified-diff candidates | Approve generation |
| 3. Evaluate | Creates isolated Git worktrees, validates patches, and runs focused tests | Approve evaluation |
| 4. Select | Scores candidates by test result, confidence, and changed-file count | Choose the best patch |
| 5. Publish | Creates a branch in your fork and opens a draft PR to upstream | Final approval |

## Safety guarantees

- Every state-changing workflow stage has an explicit approval checkpoint.
- Every proposed patch is validated with `git apply --check` before it runs.
- Each candidate is applied in its own Git worktree, separate from the main clone.
- A draft PR is blocked unless the selected candidate's verification test passes.
- `.env`, virtual environments, cloned repositories, candidate worktrees, and tool caches are ignored by Git.
- Aggregate CI reports without a single reproducible failure are labelled **Needs reproduction** instead of receiving a guessed patch.

## Quick start (WSL / Linux)

Requirements: Python 3.11+, Git, and a GitHub Personal Access Token with repository write permission for draft-PR creation.

```bash
cd /mnt/d/App/fixly
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn fixforge.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://localhost:8000](http://localhost:8000). FastAPI's interactive API reference is available at [http://localhost:8000/docs](http://localhost:8000/docs).

## Configuration

Copy `.env.example` to `.env` and configure the values below. Never commit `.env`.

```dotenv
# Codex analysis
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.3-codex
OPENAI_REASONING_EFFORT=xhigh

# GitHub API access and the fork FixForge is allowed to push to
GITHUB_TOKEN=...
FIXFORGE_FORK_REPOSITORY=sharmax-vikas/transformers

# Candidate evaluation limits
TEST_TIMEOUT_SECONDS=180
MAX_CANDIDATES=3
```

For a fine-grained GitHub token, grant the selected fork **Contents: Read and write**. Add **Pull requests: Read and write** for FixForge's final draft-PR action.

## Demo flow

Use a specific issue with a concrete technical report, for example:

```text
https://github.com/huggingface/transformers/issues/47317
```

1. Paste the issue URL and select **Create investigation**.
2. Review the cloned revision, repository evidence, and confidence assessment.
3. Approve **Generate Codex candidates**.
4. Review the candidate diffs; approve **Evaluate proposed patches**.
5. Inspect the test output and score, then choose a passing candidate.
6. Approve the final draft-PR action.

An aggregate report such as `transformers#47309` contains multiple unrelated CI failures rather than one code defect. FixForge deliberately returns **Needs reproduction** for that type of report; choose a concrete linked issue or provide focused logs and reproduction steps instead.

## Docker

```bash
docker compose up --build
```

Docker persists cloned repositories and isolated candidate workspaces in the `fixforge-workspaces` volume. Put secrets only in `.env`; it is excluded from the image source and Git history.

## REST API

| Method | Endpoint | Description |
| --- | --- | --- |
| `POST` | `/api/jobs` | Start an investigation with `{ "issue_url": "https://github.com/owner/repo/issues/123" }` |
| `GET` | `/api/jobs/{job_id}` | Read current activity, evidence, candidates, logs, score, and PR status |
| `POST` | `/api/jobs/{job_id}/approvals` | Approve a workflow action |
| `GET` | `/health` | Readiness endpoint |

Approval request example:

```json
{
  "action": "evaluate_patches"
}
```

Supported actions are `generate_patches`, `evaluate_patches`, `select_best_patch`, and `create_draft_pr`. For a manual candidate choice, include `candidate_id` with `select_best_patch`.

## Project structure

```text
fixforge/
├── config.py       # environment-backed settings
├── schemas.py      # API and workflow data models
├── services.py     # GitHub, Codex, Git workspace, and workflow services
├── main.py         # FastAPI application and REST routes
└── static/         # approval-first web interface
```

## Test environments

FixForge includes `pytest` so focused Python tests can start. Repository-specific libraries are still owned by the target project. For example, a Transformers model test may additionally require:

```bash
cd .fixforge/repositories/sharmax-vikas__transformers
../../.venv/bin/pip install -e ".[torch,testing]"
```

If an environment dependency is missing, FixForge records the exact error in the candidate result and blocks PR creation rather than falsely reporting a passing verification.

## Production notes

The bundled job store is intentionally in-memory for local single-user use. Before deploying multi-user production workloads, replace it with durable storage (Postgres), a background queue (for example Redis/Celery), scoped per-job sandboxes, and GitHub App installation tokens.
