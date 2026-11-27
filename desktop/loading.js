// Draws whatever the app says the state is. The app owns the state; this page
// keeps only a clock for the elapsed time.

const bridge = window.guaardvarkDesktop;
const $ = (id) => document.getElementById(id);
const VISIBLE_STEPS = 7;

let startedAt = null;

function drawSteps(list, steps, visible = VISIBLE_STEPS) {
  list.replaceChildren(...(steps || []).slice(-visible).map((step) => {
    const li = document.createElement('li');
    li.className = step.state;
    li.textContent = step.label;
    return li;
  }));
}

function drawElapsed() {
  if (startedAt === null) return;
  const s = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  $('elapsed').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function draw(ui) {
  if (!ui) return;
  const view = ui.view === 'checking' ? 'starting' : ui.view;
  for (const id of ['starting', 'failed', 'stopping', 'install', 'restart']) $(id).hidden = id !== view;
  startedAt = view === 'starting' && ui.startedAt ? ui.startedAt : null;

  if (view === 'starting') {
    $('label').textContent = ui.label || 'Starting Guaardvark';
    $('detail').textContent = ui.detail || '';
    const known = typeof ui.pct === 'number';
    $('bar').classList.toggle('busy', !known);
    $('fill').style.width = known ? `${ui.pct}%` : '';
    if (known) $('bar').setAttribute('aria-valuenow', String(Math.round(ui.pct)));
    else $('bar').removeAttribute('aria-valuenow');
    drawSteps($('steps'), ui.steps);
    $('elapsed').textContent = '';
    drawElapsed();
  } else if (view === 'failed') {
    $('fail-label').textContent = ui.label;
    $('fail-detail').textContent = ui.detail || '';
    drawSteps($('fail-steps'), ui.steps);
  } else if (view === 'install') {
    drawSteps($('install-steps'), ui.plan.steps, Infinity);
    $('install-notes').replaceChildren(...ui.plan.notes.map((note) => {
      const li = document.createElement('li');
      li.textContent = note;
      return li;
    }));
  } else if (view === 'restart') {
    $('restart-detail').textContent = ui.detail || '';
  }
}

$('retry').addEventListener('click', () => bridge.retry());
$('show-log').addEventListener('click', () => bridge.showLog());
$('quit').addEventListener('click', () => bridge.quit());
$('install-go').addEventListener('click', () => bridge.install());
$('install-quit').addEventListener('click', () => bridge.quit());
$('restart-now').addEventListener('click', () => bridge.restart());
$('restart-later').addEventListener('click', () => bridge.quit());

setInterval(drawElapsed, 500);
bridge.onUi(draw);
bridge.getUi().then(draw);
