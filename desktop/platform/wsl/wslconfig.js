// The keep-alive setting in the Windows user's %UserProfile%\.wslconfig.
//
// WSL stops a distribution about 15 s after its last wsl.exe session ends
// ([general] instanceIdleTimeout, default 15000) and the VM a minute later
// ([wsl2] vmIdleTimeout, default 60000); -1 turns each off
// (https://learn.microsoft.com/windows/wsl/wsl-config). While the app is
// open it holds a session itself, so these only matter for running
// Guaardvark on after the window closes. They apply to every distribution
// on the PC and take effect after `wsl --shutdown`. Same edit as
// scripts/platform/wsl.sh: other lines are kept and the old file is saved
// as .wslconfig.before-guaardvark.

const fs = require('node:fs');
const path = require('node:path');

const WANT = {
  general: ['instanceIdleTimeout', '-1'],
  wsl2: ['vmIdleTimeout', '-1'],
};

function configPath(userProfile) {
  return path.join(userProfile, '.wslconfig');
}

function readLines(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').replace(/^﻿/, '').split(/\r?\n/);
}

function parse(lines) {
  let section = '';
  const found = {};
  for (const raw of lines) {
    const line = raw.trim();
    const sec = /^\[(.+)\]$/.exec(line);
    if (sec) { section = sec[1].trim().toLowerCase(); continue; }
    const kv = /^([A-Za-z0-9_.]+)\s*=\s*(.*)$/.exec(line);
    if (kv) found[`${section}.${kv[1].toLowerCase()}`] = kv[2].trim();
  }
  return found;
}

function isKeepAlive(userProfile) {
  const found = parse(readLines(configPath(userProfile)));
  const negative = (k) => Number.isInteger(Number(found[k])) && Number(found[k]) < 0;
  return negative('general.instanceidletimeout') && negative('wsl2.vmidletimeout');
}

function rewrite(lines, wanted) {
  const out = [];
  const done = new Set();
  let section = null;
  const close = (sec) => {
    if (sec in wanted && !done.has(sec)) {
      while (out.length && !out[out.length - 1].trim()) out.pop();
      const [key, value] = wanted[sec];
      if (value !== null) out.push(`${key}=${value}`);
      out.push('');
      done.add(sec);
    }
  };
  for (const line of lines) {
    const sec = /^\s*\[(.+)\]\s*$/.exec(line);
    if (sec) {
      close(section);
      section = sec[1].trim().toLowerCase();
      out.push(line);
      continue;
    }
    if (section in wanted) {
      const [key, value] = wanted[section];
      if (new RegExp(`^\\s*${key}\\s*=`, 'i').test(line)) {
        if (value !== null) out.push(`${key}=${value}`);
        done.add(section);
        continue;
      }
    }
    out.push(line);
  }
  close(section);
  for (const [sec, [key, value]] of Object.entries(wanted)) {
    if (!done.has(sec) && value !== null) {
      if (out.length && out[out.length - 1].trim()) out.push('');
      out.push(`[${sec}]`, `${key}=${value}`, '');
    }
  }
  return `${out.join('\r\n').replace(/(\r\n)+$/, '')}\r\n`;
}

// on=true writes both timeouts as -1; on=false removes the two lines,
// returning WSL to its defaults. Returns whether the file changed.
function setKeepAlive(userProfile, on) {
  const file = configPath(userProfile);
  if (isKeepAlive(userProfile) === on) return false;
  const lines = readLines(file);
  // The first backup is the person's own file; later toggles keep it.
  if (lines.length && !fs.existsSync(`${file}.before-guaardvark`)) fs.copyFileSync(file, `${file}.before-guaardvark`);
  const wanted = on ? WANT : { general: [WANT.general[0], null], wsl2: [WANT.wsl2[0], null] };
  fs.writeFileSync(file, rewrite(lines, wanted));
  return true;
}

module.exports = { configPath, isKeepAlive, setKeepAlive };
