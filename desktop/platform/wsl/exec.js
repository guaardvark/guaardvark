// Runs the Windows tools the WSL setup needs. Every tool path comes from one
// table so a stand-in can replace it, and every call is written to the setup
// log with its exit code.

const { spawn } = require('node:child_process');
const path = require('node:path');
const { decode } = require('./decode');

function defaultTools(env = process.env) {
  const sys32 = path.win32.join(env.SystemRoot || 'C:\\Windows', 'System32');
  return {
    wsl: path.win32.join(sys32, 'wsl.exe'),
    reg: path.win32.join(sys32, 'reg.exe'),
    powershell: path.win32.join(sys32, 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
    shutdown: path.win32.join(sys32, 'shutdown.exe'),
    // Installed by the NVIDIA driver into System32 on current (DCH) drivers.
    nvidiaSmi: path.win32.join(sys32, 'nvidia-smi.exe'),
  };
}

function createRunner({ tools, env, log }) {
  const baseEnv = { ...process.env, ...env, WSL_UTF8: '1' };

  // Resolves with { code, stdout, stderr } (decoded) and never rejects for a
  // non-zero exit; a tool that cannot be started gives code null and `error`.
  function run(tool, args, opts = {}) {
    const exe = tools[tool] || tool;
    return new Promise((resolve) => {
      let child;
      try {
        child = spawn(exe, args, {
          env: { ...baseEnv, ...(opts.env || {}) },
          windowsHide: true,
          stdio: [opts.input ? 'pipe' : 'ignore', 'pipe', 'pipe'],
        });
      } catch (error) {
        resolve({ code: null, stdout: '', stderr: '', error });
        return;
      }
      const out = [];
      const err = [];
      let timer = null;
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        log?.(`$ ${path.basename(exe)} ${args.join(' ')}  -> ${result.code}${result.error ? ` (${result.error.message})` : ''}`);
        const text = `${result.stdout}${result.stderr}`.trim();
        if (text) log?.(text.split('\n').map((l) => `    ${l}`).join('\n'));
        resolve(result);
      };
      child.stdout.on('data', (d) => { out.push(d); opts.onStdout?.(d); });
      child.stderr.on('data', (d) => err.push(d));
      child.on('error', (error) => finish({ code: null, stdout: '', stderr: '', error }));
      child.on('close', (code) => finish({
        // wsl.exe exits with -1 on most errors; Windows hands that back as
        // 4294967295.
        code: code > 0x7fffffff ? code - 0x100000000 : code,
        stdout: opts.raw ? Buffer.concat(out).toString('utf8') : decode(Buffer.concat(out)),
        stderr: opts.raw ? Buffer.concat(err).toString('utf8') : decode(Buffer.concat(err)),
      }));
      if (opts.timeoutMs) {
        timer = setTimeout(() => {
          child.kill();
          finish({ code: null, stdout: decode(Buffer.concat(out)), stderr: '', error: new Error(`timed out after ${opts.timeoutMs} ms`) });
        }, opts.timeoutMs);
      }
      if (opts.input) {
        child.stdin.on('error', () => {});
        if (typeof opts.input.pipe === 'function') opts.input.pipe(child.stdin);
        else child.stdin.end(opts.input);
      }
    });
  }

  // Starts a long-running tool and returns the ChildProcess.
  function start(tool, args, opts = {}) {
    const exe = tools[tool] || tool;
    log?.(`$ ${path.basename(exe)} ${args.join(' ')}  (background)`);
    return spawn(exe, args, {
      env: { ...baseEnv, ...(opts.env || {}) },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  }

  return { run, start, tools };
}

module.exports = { createRunner, defaultTools };
