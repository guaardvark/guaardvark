// Runs one command as administrator. Windows shows its permission prompt
// (UAC); the command's output goes to a file because an elevated process
// cannot write to this process's pipes.

const fs = require('node:fs');
const { decode } = require('./decode');

// Exit code when the person answers No to the permission prompt. The script
// also prints a marker, which does not depend on how the exit code travels.
const ERROR_CANCELLED = 1223;
const DECLINED_MARKER = 'GV_UAC_DECLINED';
// Exit codes Windows installers use for "done, restart to finish".
const RESTART_CODES = new Set([3010, 1641]);

function encodePowerShell(script) {
  return Buffer.from(script, 'utf16le').toString('base64');
}

// The values reach the script through the environment of the unelevated
// PowerShell, which builds the cmd.exe line before asking for elevation.
// cmd /s strips the outer quotes, so the inner ones survive.
const ELEVATE_SCRIPT = [
  '#gv:elevate',
  "$ErrorActionPreference = 'Stop'",
  "$cmd = Join-Path $env:SystemRoot 'System32\\cmd.exe'",
  "$line = '/d /s /c \"set WSL_UTF8=1&& \"' + $env:GV_ELEVATE_EXE + '\" ' + $env:GV_ELEVATE_ARGS + ' > \"' + $env:GV_ELEVATE_OUT + '\" 2>&1\"'",
  'try {',
  '  $p = Start-Process -FilePath $cmd -ArgumentList $line -Verb RunAs -WindowStyle Hidden -Wait -PassThru',
  '  exit $p.ExitCode',
  '} catch {',
  '  $e = $_.Exception',
  "  while ($e) { if ($e.NativeErrorCode -eq 1223) { Write-Output 'GV_UAC_DECLINED'; exit 1223 }; $e = $e.InnerException }",
  '  Write-Output $_.Exception.Message',
  '  exit 1',
  '}',
].join('\n');

const SAFE_ARG = /^[A-Za-z0-9._:=-]+$/;

async function runElevated(runner, tool, args, outFile) {
  for (const a of args) {
    if (!SAFE_ARG.test(a)) throw new Error(`refusing to elevate an argument with special characters: ${a}`);
  }
  try { fs.rmSync(outFile, { force: true }); } catch { /* not there */ }
  const res = await runner.run('powershell', ['-NoProfile', '-NonInteractive', '-EncodedCommand', encodePowerShell(ELEVATE_SCRIPT)], {
    env: { GV_ELEVATE_EXE: runner.tools[tool] || tool, GV_ELEVATE_ARGS: args.join(' '), GV_ELEVATE_OUT: outFile },
  });
  let output = '';
  try { output = decode(fs.readFileSync(outFile)); } catch { /* the command never ran */ }
  return {
    code: res.code,
    output: output || res.stdout,
    declined: res.code === ERROR_CANCELLED || res.stdout.includes(DECLINED_MARKER),
    restartRequested: RESTART_CODES.has(res.code),
  };
}

module.exports = { runElevated, encodePowerShell, ERROR_CANCELLED, ELEVATE_SCRIPT };
