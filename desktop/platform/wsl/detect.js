// Read-only checks of the PC: WSL, its distributions, virtualization, a
// pending restart and the NVIDIA driver. wsl.exe's message text is
// translated into the Windows display language, so nothing here depends on
// English wording: exit codes, version numbers, distribution names and the
// untranslated "Error code: Wsl/..." tokens carry the meaning.

const os = require('node:os');
const { lines } = require('./decode');
const { classify } = require('./labels');
const { encodePowerShell } = require('./elevate');

// WSL 2 with GPU passthrough needs Windows 10 21H2 (19044) or Windows 11.
const MIN_WINDOWS_BUILD = 19044;

function windowsBuild(release = os.release()) {
  const m = /^\d+\.\d+\.(\d+)/.exec(release || '');
  return m ? Number(m[1]) : null;
}

function parseVersion(text) {
  const m = /(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?/.exec(text || '');
  return m ? m.slice(1, 5).map((n) => Number(n || 0)) : null;
}

function versionAtLeast(have, want) {
  if (!have) return false;
  for (let i = 0; i < want.length; i += 1) {
    if ((have[i] || 0) !== want[i]) return (have[i] || 0) > want[i];
  }
  return true;
}

// state: 'absent'  only the Windows stub is present (`wsl --version` fails),
//        'broken'  WSL answers but reports an error code (classified),
//        'ready'   WSL answers `--version` and reports no error code.
// A just-enabled WSL can read 'ready' before the restart that activates the
// Virtual Machine Platform; the setup state file covers that case.
async function wslState(runner) {
  const ver = await runner.run('wsl', ['--version'], { timeoutMs: 30000 });
  if (ver.code === null && ver.error) {
    return { state: 'absent', reason: `wsl.exe could not be started: ${ver.error.message}` };
  }
  const version = ver.code === 0 ? parseVersion(lines(ver.stdout)[0]) : null;
  const status = await runner.run('wsl', ['--status'], { timeoutMs: 30000 });
  const statusText = `${status.stdout}\n${status.stderr}`;
  const problem = classify(`${ver.stdout}\n${statusText}`);
  if (!version) return { state: 'absent', version: null, problem, statusText };
  // Some releases exit non-zero from `--status` while no distribution is
  // installed yet; only a recognised error code counts as a fault.
  if (problem) return { state: 'broken', version, problem, statusText };
  return { state: 'ready', version, problem: null, statusText, statusCode: status.code };
}

// Distribution names, from `wsl --list --quiet`. With none installed WSL
// prints a message and exits non-zero; names never contain spaces.
async function distros(runner, { running = false } = {}) {
  const args = running ? ['--list', '--running', '--quiet'] : ['--list', '--quiet'];
  const res = await runner.run('wsl', args, { timeoutMs: 30000 });
  if (res.code !== 0) return [];
  return lines(res.stdout).map((l) => l.trim()).filter((l) => /^[A-Za-z0-9._-]+$/.test(l));
}

// WSL version (1 or 2) of each distribution, from `wsl --list --verbose`.
// Columns: [*] NAME STATE VERSION; STATE is translated and may contain
// spaces, so only the first and last fields are used.
async function distroVersions(runner) {
  const res = await runner.run('wsl', ['--list', '--verbose'], { timeoutMs: 30000 });
  const out = {};
  if (res.code !== 0) return out;
  for (const line of lines(res.stdout).slice(1)) {
    const fields = line.replace(/^\s*\*?\s*/, '').trim().split(/\s+/);
    if (fields.length >= 3 && /^[12]$/.test(fields[fields.length - 1])) {
      out[fields[0]] = Number(fields[fields.length - 1]);
    }
  }
  return out;
}

// Firmware virtualization and whether a hypervisor already runs. With a
// hypervisor running (Hyper-V, WSL itself) Windows reports the firmware flag
// as false, so `hypervisor` decides first.
const VIRT_SCRIPT = [
  '#gv:virtualization',
  "$ErrorActionPreference = 'Stop'",
  '$cs = Get-CimInstance Win32_ComputerSystem',
  '$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1',
  '@{ hypervisor = [bool]$cs.HypervisorPresent; firmware = [bool]$cpu.VirtualizationFirmwareEnabled } | ConvertTo-Json -Compress',
].join('\n');

async function virtualization(runner) {
  const res = await runner.run('powershell', ['-NoProfile', '-NonInteractive', '-EncodedCommand', encodePowerShell(VIRT_SCRIPT)], { timeoutMs: 60000 });
  try {
    const v = JSON.parse(res.stdout.trim().split('\n').pop());
    return { known: true, hypervisor: !!v.hypervisor, firmware: !!v.firmware, ok: !!v.hypervisor || !!v.firmware };
  } catch {
    return { known: false, ok: true };
  }
}

// Windows sets this key while enabling an optional feature waits for a
// restart. `reg query` exits 0 when the key exists and 1 when it does not.
const REBOOT_KEY = 'HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending';

async function rebootPending(runner) {
  const res = await runner.run('reg', ['query', REBOOT_KEY], { timeoutMs: 15000 });
  return res.code === 0;
}

// Windows-side NVIDIA driver. WSL hands the card to Linux through this
// driver; the Linux system needs no NVIDIA driver of its own.
//
// The Guaardvark image's PyTorch needs a driver from the 580 branch or
// newer. With an older driver the image's first start installs a PyTorch
// build that matches it (packaging/wsl-image), which is a large extra
// download, so the install plan asks the person to update first.
const MIN_NVIDIA_DRIVER = 580;

function driverMajor(version) {
  const m = /^(\d+)/.exec(version || '');
  return m ? Number(m[1]) : null;
}

async function nvidia(runner) {
  const query = (fields) => runner.run('nvidiaSmi', [`--query-gpu=${fields}`, '--format=csv,noheader,nounits'], { timeoutMs: 20000 });
  // compute_cap is a newer field; drivers that lack it refuse the whole query.
  let withCap = true;
  let res = await query('name,driver_version,memory.total,compute_cap');
  if (res.code !== 0) {
    withCap = false;
    res = await query('name,driver_version,memory.total');
  }
  if (res.code !== 0) return { present: false, status: 'none' };
  const gpus = lines(res.stdout).map((l) => {
    const [name, driver, memMiB, cap] = l.split(',').map((x) => x.trim());
    return { name, driver, memoryMiB: Number(memMiB) || null, computeCap: withCap ? cap || null : null };
  });
  if (!gpus.length) return { present: false, status: 'none' };
  const major = driverMajor(gpus[0].driver);
  return {
    present: true,
    gpus,
    driver: gpus[0].driver,
    minDriver: MIN_NVIDIA_DRIVER,
    status: major !== null && major < MIN_NVIDIA_DRIVER ? 'old-driver' : 'ok',
  };
}

module.exports = {
  MIN_WINDOWS_BUILD,
  MIN_NVIDIA_DRIVER,
  REBOOT_KEY,
  windowsBuild,
  parseVersion,
  versionAtLeast,
  wslState,
  distros,
  distroVersions,
  virtualization,
  rebootPending,
  nvidia,
};
