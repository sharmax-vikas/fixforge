const form = document.querySelector('#job-form');
const notice = document.querySelector('#notice');
const jobSection = document.querySelector('#job');
const actionFeedback = document.querySelector('#action-feedback');
let job = null;
let timer = null;
let progressTimer = null;
let actionStartedAt = null;

const stages = [
  ['awaiting_analysis_approval', 'Generate Codex candidates', 'Generate independent root-cause and patch proposals.', 'generate_patches'],
  ['awaiting_patch_approval', 'Evaluate proposed patches', 'Apply every candidate in an isolated worktree and run its test command.', 'evaluate_patches'],
  ['awaiting_selection', 'Select the best patch', 'Choose the highest-scoring candidate or select another evaluated candidate.', 'select_best_patch'],
  ['awaiting_pr_approval', 'Create draft pull request', 'Push only to your configured fork and open a draft PR against upstream.', 'create_draft_pr'],
];

function message(value, kind = '') { notice.textContent = value; notice.className = `notice ${kind}`; }
function text(selector, value) { document.querySelector(selector).textContent = value || 'Not available.'; }
function showAction(title, detail, state = 'working') {
  text('#feedback-title', title); text('#feedback-detail', detail);
  actionFeedback.className = `action-feedback ${state}`;
}
function clearAction() {
  actionFeedback.className = 'action-feedback hidden';
  clearInterval(progressTimer); progressTimer = null; actionStartedAt = null;
}
function runningAction() {
  return { generation_running: 'generate_patches', evaluation_running: 'evaluate_patches', pr_running: 'create_draft_pr' }[job?.state];
}
function beginAction(label) {
  actionStartedAt = Date.now();
  showAction('Approval received', `${label} has started. Contacting the local agent...`);
  clearInterval(progressTimer);
  progressTimer = setInterval(() => {
    const seconds = Math.floor((Date.now() - actionStartedAt) / 1000);
    showAction('Working', `${label} is still running (${seconds}s). ${job?.activity || 'Waiting for an update...'}`);
  }, 1000);
}

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed.');
  return data;
}

function render() {
  if (!job) return;
  jobSection.classList.remove('hidden');
  text('#title', job.issue?.title || 'Preparing investigation');
  text('#activity', job.activity);
  text('#state', job.state.replaceAll('_', ' '));
  text('#metrics', `Job ${job.id} | started ${new Date(job.metrics.started_at).toLocaleTimeString()}`);
  if (job.issue) {
    const link = document.querySelector('#issue-link');
    link.href = job.issue.url; link.textContent = `${job.issue.repository} #${job.issue.number}`;
    text('#issue-body', job.issue.body);
  }
  if (job.repository) {
    text('#repository', `${job.repository.fork} -> ${job.repository.upstream} @ ${job.repository.revision}`);
    text('#evidence', job.repository.evidence.map(item => `- ${item}`).join('\n') || 'No source match found.');
  }
  text('#root-cause', job.suspected_root_cause);
  text('#confidence', job.root_cause_confidence ? `Confidence: ${job.root_cause_confidence}%` : 'Confidence will be calculated after Codex analysis.');
  renderApprovals(); renderCandidates(); renderPr();
  if (job.error) { clearAction(); showAction('Action failed', job.error, 'error'); message(job.error, 'error'); }
  else if (runningAction()) showAction('Working', job.activity || 'The local agent is processing your approved action.');
  else if (job.state === 'needs_reproduction') {
    clearInterval(progressTimer); progressTimer = null; actionStartedAt = null;
    showAction('Needs reproduction', job.activity, 'diagnostics');
  }
  else clearAction();
}

function renderApprovals() {
  const holder = document.querySelector('#approval-list'); holder.textContent = '';
  stages.forEach(([state, label, description, action]) => {
    const row = document.createElement('div'); row.className = 'approval';
    row.innerHTML = `<div><strong>${label}</strong><p>${description}</p></div>`;
    if (runningAction() === action) {
      const running = document.createElement('span'); running.className = 'running'; running.textContent = 'RUNNING...'; row.append(running);
    } else if (job.state === state) {
      const button = document.createElement('button'); button.textContent = action === 'select_best_patch' ? 'Select best' : 'Approve';
      button.onclick = () => approve(action, button); row.append(button);
    } else {
      const done = document.createElement('span');
      const currentIndex = stages.findIndex(item => item[0] === job.state);
      const stageIndex = stages.findIndex(item => item[0] === state);
      done.textContent = currentIndex > stageIndex || job.state === 'completed' ? 'COMPLETED' : 'LOCKED'; row.append(done);
    }
    holder.append(row);
  });
}

function renderCandidates() {
  const section = document.querySelector('#candidates'); const holder = document.querySelector('#candidate-list'); holder.textContent = '';
  if (!job.candidates.length) { section.classList.add('hidden'); return; }
  section.classList.remove('hidden');
  job.candidates.forEach(candidate => {
    const card = document.createElement('article'); card.className = 'card candidate';
    const score = candidate.score === null ? 'Not evaluated' : `${candidate.score.toFixed(1)} score`;
    card.innerHTML = `<div class="candidate-head"><h3>${candidate.id}</h3><span>${candidate.status} | ${score}</span></div><p>${candidate.summary}</p><p><b>Confidence:</b> ${candidate.confidence}% | <b>Files:</b> ${candidate.modified_files}</p><pre>${candidate.patch || 'No patch available.'}</pre><p><b>Test:</b> ${candidate.test_command}</p><pre>${candidate.test_output || 'Not run.'}</pre>`;
    if (job.state === 'awaiting_selection' && candidate.status !== 'rejected') {
      const button = document.createElement('button'); button.textContent = 'Select this patch';
      button.onclick = () => approve('select_best_patch', button, candidate.id); card.append(button);
    }
    holder.append(card);
  });
}

function renderPr() {
  const section = document.querySelector('#pr');
  if (!job.pull_request) { section.classList.add('hidden'); return; }
  section.classList.remove('hidden'); text('#pr-title', job.pull_request.title); text('#pr-body', job.pull_request.body);
  const link = document.querySelector('#pr-link'); link.href = job.pull_request.url || '#'; link.textContent = job.pull_request.url || '';
}

async function approve(action, button, candidateId) {
  const label = button.closest('.approval, .candidate')?.querySelector('strong, h3')?.textContent || 'Approved action';
  button.disabled = true; button.textContent = 'Starting...'; beginAction(label); message('Approval received. FixForge is working...', 'working');
  try {
    job = await request(`/api/jobs/${job.id}/approvals`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action, candidate_id: candidateId}) });
    render(); poll();
  } catch (error) {
    clearAction(); showAction('Action failed', error.message, 'error'); message(error.message, 'error'); render();
  }
}

async function poll() {
  clearTimeout(timer);
  if (!job || ['completed', 'failed', 'needs_reproduction', 'awaiting_analysis_approval', 'awaiting_patch_approval', 'awaiting_selection', 'awaiting_pr_approval'].includes(job.state)) return;
  try {
    job = await request(`/api/jobs/${job.id}`); render(); timer = setTimeout(poll, 1500);
  } catch (error) {
    clearAction(); showAction('Status check failed', error.message, 'error'); message(error.message, 'error');
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault(); const button = form.querySelector('button'); button.disabled = true; message('Starting repository investigation...', 'working');
  try {
    job = await request('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({issue_url: document.querySelector('#issue-url').value})});
    render(); poll();
  } catch (error) { message(error.message, 'error'); }
  finally { button.disabled = false; }
});
