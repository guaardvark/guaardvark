// Undoes Guaardvark's changes to %UserProfile%\.wslconfig (keep-alive, the
// memory limit) for the uninstaller, which starts the app as
// `Guaardvark.exe --undo-wsl-settings <result file>` (see windows.js). The
// most recent change is undone first. The message for the person, if any,
// goes to the result file as UTF-16LE (what NSIS's FileReadUTF16LE reads).
// Returns the exit code: 0 done or nothing to do; 3 done, show the message;
// 1 failed (the message says why).

const fs = require('node:fs');
const path = require('node:path');
const { undoChanges } = require('./wslconfig');

const FLAG = '--undo-wsl-settings';
const RECORDS = ['keep-alive.json', 'memory.json'];

function recordFiles(localAppData) {
  const dir = path.join(localAppData, 'Guaardvark');
  return RECORDS.map((name) => path.join(dir, name))
    .filter((file) => fs.existsSync(file))
    .map((file) => {
      let at = '';
      try { at = JSON.parse(fs.readFileSync(file, 'utf8')).at || ''; } catch { /* undone as found */ }
      return { file, at };
    })
    .sort((a, b) => (a.at < b.at ? 1 : -1))
    .map((r) => r.file);
}

function runUndo(resultFile, env = process.env) {
  let code = 0;
  const messages = [];
  try {
    if (!env.USERPROFILE || !env.LOCALAPPDATA) throw new Error('USERPROFILE or LOCALAPPDATA is not set');
    for (const file of recordFiles(env.LOCALAPPDATA)) {
      const res = undoChanges(env.USERPROFILE, file);
      if (res.message) messages.push(res.message);
      if (res.notify) code = 3;
    }
  } catch (err) {
    messages.push(`Could not undo Guaardvark's changes to .wslconfig: ${err.message}`);
    code = 1;
  }
  if (resultFile) {
    try { fs.writeFileSync(resultFile, Buffer.from(messages.join(' ').replace(/\s*\n\s*/g, ' '), 'utf16le')); } catch { /* the exit code still says it */ }
  }
  return code;
}

module.exports = { runUndo, FLAG, RECORDS };
