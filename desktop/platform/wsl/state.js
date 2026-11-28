// What the Windows setup has done so far, kept across app launches and the
// restart Windows needs after turning WSL on.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const RUNONCE_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce';
const RUNONCE_VALUE = 'GuaardvarkSetup';
// The app is started with this flag after the restart; it opens normally and
// the setup carries on from the saved state.
const RESUME_FLAG = '--resume-setup';

function createStateStore(file) {
  function read() {
    try {
      return JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch {
      return {};
    }
  }
  function write(patch) {
    const next = { ...read(), ...patch, updatedAt: new Date().toISOString() };
    for (const [k, v] of Object.entries(next)) if (v === undefined) delete next[k];
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, `${JSON.stringify(next, null, 2)}\n`);
    fs.renameSync(tmp, file);
    return next;
  }
  return { read, write, file };
}

function systemBootTime() {
  return Date.now() - os.uptime() * 1000;
}

// HKCU RunOnce runs at the next sign-in of this user and needs no
// administrator rights. Windows deletes the value before running it.
async function registerResume(runner, exePath) {
  const res = await runner.run('reg', ['add', RUNONCE_KEY, '/v', RUNONCE_VALUE, '/t', 'REG_SZ', '/d', `"${exePath}" ${RESUME_FLAG}`, '/f'], { timeoutMs: 15000 });
  return res.code === 0;
}

async function clearResume(runner) {
  await runner.run('reg', ['delete', RUNONCE_KEY, '/v', RUNONCE_VALUE, '/f'], { timeoutMs: 15000 });
}

module.exports = { createStateStore, systemBootTime, registerResume, clearResume, RESUME_FLAG, RUNONCE_KEY, RUNONCE_VALUE };
