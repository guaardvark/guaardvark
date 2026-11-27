// Undoes Guaardvark's keep-alive change to %UserProfile%\.wslconfig for the
// uninstaller, which starts the app as `Guaardvark.exe --undo-keep-alive
// <result file>` (see windows.js). The message for the person, if any, goes
// to the result file as UTF-16LE (what NSIS's FileReadUTF16LE reads).
// Returns the exit code: 0 done or nothing to do; 3 done, show the message;
// 1 failed (the message says why).

const fs = require('node:fs');
const path = require('node:path');
const { undoKeepAlive } = require('./wslconfig');

const FLAG = '--undo-keep-alive';

function runUndo(resultFile, env = process.env) {
  let code;
  let message = '';
  try {
    if (!env.USERPROFILE || !env.LOCALAPPDATA) throw new Error('USERPROFILE or LOCALAPPDATA is not set');
    const res = undoKeepAlive(env.USERPROFILE, path.join(env.LOCALAPPDATA, 'Guaardvark', 'keep-alive.json'));
    message = res.message || '';
    code = res.notify ? 3 : 0;
  } catch (err) {
    message = `Could not undo the keep-alive change: ${err.message}`;
    code = 1;
  }
  if (resultFile) {
    try { fs.writeFileSync(resultFile, Buffer.from(message.replace(/\s*\n\s*/g, ' '), 'utf16le')); } catch { /* the exit code still says it */ }
  }
  return code;
}

module.exports = { runUndo, FLAG };
