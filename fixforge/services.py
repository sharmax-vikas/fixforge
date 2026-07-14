from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx

from .config import Settings
from .schemas import Candidate, Issue, Job, PullRequestDraft, RepositoryContext

ISSUE_URL = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)/?$", re.I)


def run(command: list[str], cwd: Path, timeout: int = 90, environment: dict[str, str] | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False, env=environment)
        return result.returncode == 0, (result.stdout + "\n" + result.stderr).strip()[-12000:]
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)


class GitHubService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "fixforge"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        response = httpx.request(method, f"https://api.github.com{path}", headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def issue_from_url(self, url: str) -> Issue:
        match = ISSUE_URL.match(url)
        if not match:
            raise ValueError("Use a GitHub issue URL like https://github.com/owner/repository/issues/123.")
        owner, repository, number = match.groups()
        data = self._request("GET", f"/repos/{owner}/{repository}/issues/{number}")
        if "pull_request" in data:
            raise ValueError("The supplied URL is a pull request, not an issue.")
        return Issue(
            url=data["html_url"], number=int(number), title=data["title"], body=data.get("body") or "",
            repository=f"{owner}/{repository}", labels=[label["name"] for label in data.get("labels", [])],
        )

    def repository(self, full_name: str) -> dict:
        return self._request("GET", f"/repos/{full_name}")

    def create_draft_pr(self, fork: str, upstream: str, base: str, branch: str, title: str, body: str) -> str:
        owner = fork.split("/", 1)[0]
        data = self._request("POST", f"/repos/{upstream}/pulls", {
            "title": title, "body": body, "head": f"{owner}:{branch}", "base": base, "draft": True,
        })
        return data["html_url"]


class WorkspaceService:
    def __init__(self, settings: Settings):
        self.root = settings.workspace_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def checkout(self, repository: str) -> tuple[Path, str]:
        destination = self.root / "repositories" / repository.replace("/", "__")
        if not (destination / ".git").exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            ok, output = run(["git", "clone", "--depth", "1", f"https://github.com/{repository}.git", str(destination)], self.root, 240)
            if not ok:
                raise RuntimeError(f"Clone failed: {output}")
        ok, revision = run(["git", "rev-parse", "--short", "HEAD"], destination)
        if not ok:
            raise RuntimeError(f"Could not read cloned revision: {revision}")
        return destination, revision

    def structure_and_evidence(self, checkout: Path, issue: Issue) -> tuple[list[str], list[str]]:
        ok, files = run(["git", "ls-files"], checkout)
        structure = files.splitlines()[:180] if ok else []
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", f"{issue.title} {issue.body}")
        ignored = {"this", "that", "with", "from", "where", "which", "issue", "error", "model", "using", "labels", "loss"}
        evidence: list[str] = []
        for word in dict.fromkeys(word.lower() for word in words if word.lower() not in ignored):
            ok, output = run(["git", "grep", "-n", "-i", "-m", "1", "--", word], checkout)
            if ok and output:
                evidence.append(output.splitlines()[0][:300])
            if len(evidence) >= 8:
                break
        return structure, evidence

    def candidate_workspace(self, job_id: str, candidate_id: str, checkout: Path) -> Path:
        target = self.root / "candidates" / job_id / candidate_id
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        ok, output = run(["git", "worktree", "add", "--detach", str(target), "HEAD"], checkout, 120)
        if not ok:
            raise RuntimeError(f"Could not create isolated patch workspace: {output}")
        return target

    def apply_patch(self, workspace: Path, patch: str) -> tuple[bool, str, int]:
        patch_file = workspace / ".fixforge.patch"
        patch_file.write_text(patch.strip() + "\n", encoding="utf-8")
        ok, output = run(["git", "apply", "--check", str(patch_file)], workspace)
        if ok:
            ok, output = run(["git", "apply", str(patch_file)], workspace)
        patch_file.unlink(missing_ok=True)
        if not ok:
            # Codex occasionally returns a semantically correct hunk with an
            # inaccurate line-count header. Recover only when every hunk has
            # one unambiguous context match; otherwise retain git's rejection.
            fallback_ok, fallback_output = self._apply_context_hunks(workspace, patch)
            if not fallback_ok:
                return False, f"{output}\n\nContext-aware fallback: {fallback_output}", 0
        _, changed = run(["git", "diff", "--name-only"], workspace)
        return True, output or "Applied using context-aware fallback.", len([line for line in changed.splitlines() if line])

    def _apply_context_hunks(self, workspace: Path, patch: str) -> tuple[bool, str]:
        target_match = re.search(r"^\+\+\+ b/(.+)$", patch, flags=re.MULTILINE)
        if not target_match:
            return False, "No target file was found in the proposed diff."
        target = (workspace / target_match.group(1)).resolve()
        if workspace.resolve() not in target.parents or not target.is_file():
            return False, "The proposed patch targets a missing or unsafe file path."
        hunks = re.split(r"^@@[^\n]*@@[^\n]*\n", patch, flags=re.MULTILINE)[1:]
        if not hunks:
            return False, "No unified-diff hunks were found."
        lines = target.read_text(encoding="utf-8").splitlines()
        for hunk in hunks:
            old, new = [], []
            for line in hunk.splitlines():
                if line.startswith("\\ No newline"):
                    continue
                if line.startswith((" ", "-")):
                    old.append(line[1:])
                if line.startswith((" ", "+")):
                    new.append(line[1:])
            if not old:
                return False, "A hunk did not include enough original context to validate safely."
            matches = [index for index in range(len(lines) - len(old) + 1) if [value.rstrip() for value in lines[index:index + len(old)]] == [value.rstrip() for value in old]]
            if len(matches) != 1:
                return False, f"Expected one matching source context but found {len(matches)}."
            start = matches[0]
            lines[start:start + len(old)] = new
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True, "Applied after validating one exact source-context match per hunk."

    def test(self, workspace: Path, command: str, timeout: int) -> tuple[bool, str]:
        # Candidate worktrees use a src/ layout. Point the test process at the
        # patched candidate source rather than whichever package happens to be
        # installed in FixForge's own virtual environment.
        environment = os.environ.copy()
        source = workspace / "src"
        if source.is_dir():
            existing = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = f"{source}{os.pathsep}{existing}" if existing else str(source)
        return run(shlex.split(command), workspace, timeout, environment)

    def commit_and_push(self, workspace: Path, branch: str) -> None:
        for command in (["git", "config", "user.name", "FixForge"], ["git", "config", "user.email", "fixforge@local"]):
            ok, output = run(command, workspace)
            if not ok:
                raise RuntimeError(f"Could not configure the patch commit: {output}")
        for command in (["git", "checkout", "-B", branch], ["git", "add", "-A"], ["git", "commit", "-m", "fix: resolve issue with FixForge"], ["git", "push", "origin", f"HEAD:{branch}"]):
            ok, output = run(command, workspace, 180)
            if not ok:
                raise RuntimeError(f"Could not publish selected patch: {output}")


class CodexService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate(self, issue: Issue, context: RepositoryContext) -> list[Candidate]:
        if not self.settings.openai_api_key:
            return [Candidate(id="candidate-1", summary="Codex API key is required to generate a patch.", root_cause="No live Codex analysis was run.", confidence=0, patch="", test_command="pytest -q")]
        source_context = self._source_context(issue, context)
        prompt = f"""Issue: {issue.title}\n{issue.body}\nRepository: {context.fork}\nRevision: {context.revision}\nEvidence:\n{chr(10).join(context.evidence)}\n\nExact checked-out source excerpts:\n{source_context}\n\nReturn ONLY JSON: {{\"root_cause\": string, \"confidence\": integer, \"candidates\": [{{\"summary\": string, \"patch\": string, \"test_command\": string}}]}}. Generate at most 3 conservative unified diffs. Each `patch` must start exactly with `diff --git`, include `---`, `+++`, and at least one `@@` hunk header. Do not use Markdown code fences. Use exact source excerpts for hunk context; never invent files."""
        parsed = self._request_json(prompt)
        candidates = self._candidates_from_response(parsed)
        if not candidates:
            # One repair attempt is cheaper and safer than making the user
            # approve an evaluation that is guaranteed to reject every patch.
            repair = prompt + "\nYour previous response was not a valid unified diff. Return one valid candidate now, using only the exact source excerpt."
            candidates = self._candidates_from_response(self._request_json(repair))
        if not candidates:
            return []
        return candidates

    def _request_json(self, prompt: str) -> dict:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": self.settings.openai_model,
                "input": prompt,
                "reasoning": {"effort": self.settings.openai_reasoning_effort},
            },
            timeout=180,
        )
        response.raise_for_status()
        data = response.json()
        text = data.get("output_text", "")
        if not text:
            text = "".join(item.get("text", "") for output in data.get("output", []) for item in output.get("content", []) if item.get("type") in {"output_text", "text"})
        return json.loads(text.removeprefix("```json").removesuffix("```").strip())

    def _candidates_from_response(self, parsed: dict) -> list[Candidate]:
        candidates = []
        for number, item in enumerate(parsed.get("candidates", [])[: self.settings.max_candidates], start=1):
            patch = self._normalise_patch(item.get("patch", ""))
            if not self._is_unified_patch(patch):
                continue
            candidates.append(Candidate(id=f"candidate-{number}", summary=item.get("summary", "Proposed source change."), root_cause=parsed.get("root_cause", ""), confidence=max(0, min(100, int(parsed.get("confidence", 0)))), patch=patch, test_command=item.get("test_command") or "pytest -q"))
        return candidates

    @staticmethod
    def _normalise_patch(patch: str) -> str:
        patch = patch.strip()
        if patch.startswith("```"):
            patch = patch.split("\n", 1)[1] if "\n" in patch else ""
        patch = patch.removesuffix("```").strip()
        index = patch.find("diff --git")
        return patch[index:].strip() if index >= 0 else patch

    @staticmethod
    def _is_unified_patch(patch: str) -> bool:
        return bool(re.search(r"^diff --git .+\n(?:.|\n)*?^--- .+\n\+\+\+ .+\n(?:.|\n)*?^@@ .+@@", patch, flags=re.MULTILINE))

    @staticmethod
    def _source_context(issue: Issue, context: RepositoryContext) -> str:
        checkout = Path(context.checkout)
        mentioned_paths = re.findall(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py", issue.body)
        excerpts: list[str] = []
        for raw_path in dict.fromkeys(mentioned_paths):
            path = (checkout / raw_path).resolve()
            if checkout.resolve() not in path.parents or not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            anchors = ("self.loss_function", "ForCausalLMLoss", "shift_labels")
            index = next((content.find(anchor) for anchor in anchors if content.find(anchor) >= 0), 0)
            start = max(0, index - 1800)
            excerpts.append(f"--- {raw_path} (excerpt around relevant code) ---\n{content[start:index + 4500]}")
            if len(excerpts) == 3:
                break
        return "\n\n".join(excerpts) or "No direct source file was named in the issue."


class JobStore:
    def __init__(self, settings: Settings):
        self.settings, self.github, self.workspaces, self.codex = settings, GitHubService(settings), WorkspaceService(settings), CodexService(settings)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def create(self, issue_url: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], activity="Fetching issue and cloning repository")
        with self.lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._intake, args=(job.id, issue_url), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError("Job was not found or expired.")
        return job

    def approve(self, job_id: str, action: str, candidate_id: str | None) -> Job:
        job = self.get(job_id)
        transitions = {
            "generate_patches": ("awaiting_analysis_approval", self._generate),
            "evaluate_patches": ("awaiting_patch_approval", self._evaluate),
            "select_best_patch": ("awaiting_selection", self._select),
            "create_draft_pr": ("awaiting_pr_approval", self._create_pr),
        }
        expected, worker = transitions[action]
        if job.state != expected:
            raise ValueError(f"Action {action} is not available while job is {job.state}.")
        if action == "select_best_patch":
            worker(job.id, candidate_id)
        else:
            running_state = {
                "generate_patches": ("generation_running", "Generating independent Codex patch candidates"),
                "evaluate_patches": ("evaluation_running", "Applying and testing each candidate in isolated workspaces"),
                "create_draft_pr": ("pr_running", "Creating branch, pushing to fork, and opening a draft PR"),
            }
            job.state, job.activity = running_state[action]
            threading.Thread(target=worker, args=(job.id,), daemon=True).start()
        return job

    def _fail(self, job: Job, error: Exception) -> None:
        job.state, job.error, job.activity = "failed", str(error), "Workflow failed"

    def _intake(self, job_id: str, issue_url: str) -> None:
        job = self.get(job_id)
        try:
            issue = self.github.issue_from_url(issue_url)
            upstream_remote = self.github.repository(issue.repository)
            fork = self.settings.fixforge_fork_repository or issue.repository
            fork_remote = self.github.repository(fork)
            upstream = (fork_remote.get("parent") or {}).get("full_name", issue.repository)
            if upstream != issue.repository:
                raise ValueError("Configured FIXFORGE_FORK_REPOSITORY is not a fork of the issue repository.")
            checkout, revision = self.workspaces.checkout(fork)
            structure, evidence = self.workspaces.structure_and_evidence(checkout, issue)
            job.issue = issue
            job.repository = RepositoryContext(fork=fork, upstream=upstream, default_branch=upstream_remote.get("default_branch", "main"), revision=revision, checkout=str(checkout), structure=structure, evidence=evidence)
            job.suspected_root_cause = "Issue terminology was matched against the cloned source; Codex generation requires approval."
            job.root_cause_confidence = min(85, 25 + len(evidence) * 8)
            job.state, job.activity = "awaiting_analysis_approval", "Repository evidence is ready for review."
        except Exception as error:
            self._fail(job, error)

    def _generate(self, job_id: str) -> None:
        job = self.get(job_id)
        try:
            if self._requires_diagnostics(job.issue):
                job.suspected_root_cause = "This is an automated aggregate CI triage report, not one reproducible defect. It lists several unrelated failure groups, some pending their own Serge tasks and others explicitly marked no fix."
                job.root_cause_confidence = 0
                job.state = "needs_reproduction"
                job.activity = "Diagnostics required: choose one concrete failure group, collect its logs and reproduction steps, then start a focused investigation."
                return
            job.state, job.activity = "generation_running", "Generating independent Codex patch candidates"
            job.candidates = self.codex.generate(job.issue, job.repository)  # type: ignore[arg-type]
            if not job.candidates:
                job.suspected_root_cause = "The report does not contain enough exact source context or a reproducible failing path to propose a safe patch."
                job.root_cause_confidence = 0
                job.state = "needs_reproduction"
                job.activity = "Diagnostics required: add a failing reproduction, stack trace, or one concrete failure group before patch generation."
                return
            job.suspected_root_cause = job.candidates[0].root_cause
            job.root_cause_confidence = job.candidates[0].confidence
            job.state, job.activity = "awaiting_patch_approval", "Review candidates, then approve isolated patch evaluation."
        except Exception as error:
            self._fail(job, error)

    @staticmethod
    def _requires_diagnostics(issue: Issue | None) -> bool:
        if not issue:
            return True
        report = f"{issue.title}\n{issue.body}".lower()
        aggregate_signals = (
            "integration-failure triage",
            "dispatched failure groups",
            "generated by ai-assisted automation",
            "(pending)",
            "no fix",
        )
        return sum(signal in report for signal in aggregate_signals) >= 3

    def _evaluate(self, job_id: str) -> None:
        job = self.get(job_id)
        try:
            job.state, job.activity = "evaluation_running", "Applying and testing each candidate in isolated workspaces"
            checkout = Path(job.repository.checkout)  # type: ignore[union-attr]
            total = len(job.candidates)
            for number, candidate in enumerate(job.candidates, start=1):
                job.activity = f"Candidate {number}/{total}: creating isolated worktree for {candidate.id}."
                workspace = self.workspaces.candidate_workspace(job.id, candidate.id, checkout)
                candidate.workspace = str(workspace)
                job.activity = f"Candidate {number}/{total}: validating and applying {candidate.id}."
                ok, output, changed = self.workspaces.apply_patch(workspace, candidate.patch)
                candidate.modified_files = changed
                if not ok:
                    candidate.status, candidate.test_output, candidate.score = "rejected", output, -100.0
                    continue
                candidate.status = "applied"
                job.activity = f"Candidate {number}/{total}: running `{candidate.test_command}` (maximum {self.settings.test_timeout_seconds}s)."
                passed, output = self.workspaces.test(workspace, candidate.test_command, self.settings.test_timeout_seconds)
                candidate.status, candidate.test_passed, candidate.test_output = "tested", passed, output
                candidate.score = (100 if passed else 0) + candidate.confidence * 0.3 - candidate.modified_files * 4
            job.state, job.activity = "awaiting_selection", "Candidate test results are ready. Select the best patch."
        except Exception as error:
            self._fail(job, error)

    def _select(self, job_id: str, candidate_id: str | None) -> None:
        job = self.get(job_id)
        ranked = sorted(job.candidates, key=lambda item: item.score if item.score is not None else -999, reverse=True)
        candidate = next((item for item in job.candidates if item.id == candidate_id), ranked[0] if ranked else None)
        if not candidate or candidate.status == "rejected":
            raise ValueError("Choose an evaluated, applicable candidate.")
        if candidate.test_passed is not True:
            raise ValueError("Draft PR creation is blocked because this candidate's verification test did not pass.")
        candidate.status, job.selected_candidate_id = "selected", candidate.id
        job.pull_request = PullRequestDraft(title=f"fix: {job.issue.title}", body=f"## Summary\n- {candidate.summary}\n\n## Root cause\n{candidate.root_cause}\n\n## Testing\n- {'passed' if candidate.test_passed else 'failed'}: `{candidate.test_command}`")
        job.state, job.activity = "awaiting_pr_approval", "Best patch selected. Draft PR creation needs final approval."

    def _create_pr(self, job_id: str) -> None:
        job = self.get(job_id)
        try:
            job.state, job.activity = "pr_running", "Creating branch, pushing to fork, and opening a draft PR"
            candidate = next(item for item in job.candidates if item.id == job.selected_candidate_id)
            branch = f"fixforge/{job.id}"
            self.workspaces.commit_and_push(Path(candidate.workspace), branch)
            job.pull_request.url = self.github.create_draft_pr(job.repository.fork, job.repository.upstream, job.repository.default_branch, branch, job.pull_request.title, job.pull_request.body)  # type: ignore[union-attr]
            job.state, job.activity = "completed", "Draft pull request created."
        except Exception as error:
            self._fail(job, error)
