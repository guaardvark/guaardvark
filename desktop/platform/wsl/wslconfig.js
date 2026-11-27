// The keep-alive setting in the Windows user's %UserProfile%\.wslconfig.
//
// WSL stops a distribution about 15 s after its last wsl.exe session ends
// ([general] instanceIdleTimeout, default 15000) and the VM a minute later
// ([wsl2] vmIdleTimeout, default 60000); -1 turns each off
// (https://learn.microsoft.com/windows/wsl/wsl-config). While the app is
// open it holds a session itself, so these only matter for running
// Guaardvark on after the window closes. They apply to every distribution
// on the PC and take effect after `wsl --shutdown`.
//
// .wslconfig belongs to the person. Turning keep-alive on writes a record of
// exactly what changed (the file as it was, the file as written, each line
// added or replaced), and undoing it uses that record: an untouched file is
// put back byte for byte; an edited one only loses lines that still hold
// Guaardvark's value, and anything else the person changed stays. The
// uninstaller runs the same undo (keepalive-undo.js). This module must keep
// to Node built-ins: the uninstaller runs it outside the app.

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const WANT = [
  { section: 'general', key: 'instanceIdleTimeout', value: '-1' },
  { section: 'wsl2', key: 'vmIdleTimeout', value: '-1' },
];
const BACKUP_SUFFIX = '.before-guaardvark';

function configPath(userProfile) {
  return path.join(userProfile, '.wslconfig');
}

function sha256(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex');
}

function readBytes(file) {
  try {
    return fs.readFileSync(file);
  } catch (e) {
    if (e.code === 'ENOENT') return null;
    throw e;
  }
}

function splitText(buf) {
  let text = buf ? buf.toString('utf8') : '';
  const bom = text.startsWith('﻿');
  if (bom) text = text.slice(1);
  const eol = /\r\n/.test(text) || !buf ? '\r\n' : '\n';
  const lines = text.split(/\r?\n/);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return { lines, bom, eol };
}

function joinText({ lines, bom, eol }) {
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return Buffer.from(`${bom ? '﻿' : ''}${lines.join(eol)}${eol}`, 'utf8');
}

const SECTION = /^\s*\[(.+)\]\s*$/;
const keyLine = (key) => new RegExp(`^\\s*${key}\\s*=\\s*(.*?)\\s*$`, 'i');

// Index of each line's section, lower-cased ('' before the first header).
function sectionsOf(lines) {
  let section = '';
  return lines.map((line) => {
    const m = SECTION.exec(line);
    if (m) section = m[1].trim().toLowerCase();
    return section;
  });
}

function findKey(lines, section, key) {
  const secs = sectionsOf(lines);
  const re = keyLine(key);
  for (let i = 0; i < lines.length; i += 1) {
    if (secs[i] === section && !SECTION.test(lines[i])) {
      const m = re.exec(lines[i]);
      if (m) return { index: i, value: m[1] };
    }
  }
  return null;
}

function isKeepAlive(userProfile) {
  const { lines } = splitText(readBytes(configPath(userProfile)));
  const negative = (w) => {
    const hit = findKey(lines, w.section, w.key);
    return !!hit && /^-\d+$/.test(hit.value);
  };
  return WANT.every(negative);
}

function readRecord(recordFile) {
  try {
    return JSON.parse(fs.readFileSync(recordFile, 'utf8'));
  } catch {
    return null;
  }
}

// Writes both timeouts as -1 and records what changed. Returns
// { changed, reason }. Nothing is written when keep-alive is already on.
function enableKeepAlive(userProfile, recordFile) {
  const file = configPath(userProfile);
  if (isKeepAlive(userProfile)) {
    return { changed: false, reason: readRecord(recordFile) ? 'already-on' : 'set-by-you' };
  }
  const before = readBytes(file);
  const doc = splitText(before);
  const changes = [];
  for (const w of WANT) {
    const hit = findKey(doc.lines, w.section, w.key);
    if (hit) {
      changes.push({ ...w, previous: hit.value, sectionCreated: false });
      doc.lines[hit.index] = `${w.key}=${w.value}`;
      continue;
    }
    const secs = sectionsOf(doc.lines);
    const header = doc.lines.findIndex((l, i) => SECTION.test(l) && secs[i] === w.section);
    if (header >= 0) {
      // After the section's last non-blank line.
      let at = header + 1;
      for (let i = header + 1; i < doc.lines.length && secs[i] === w.section && !SECTION.test(doc.lines[i]); i += 1) {
        if (doc.lines[i].trim()) at = i + 1;
      }
      doc.lines.splice(at, 0, `${w.key}=${w.value}`);
      changes.push({ ...w, previous: null, sectionCreated: false });
    } else {
      while (doc.lines.length && !doc.lines[doc.lines.length - 1].trim()) doc.lines.pop();
      if (doc.lines.length) doc.lines.push('');
      doc.lines.push(`[${w.section}]`, `${w.key}=${w.value}`);
      changes.push({ ...w, previous: null, sectionCreated: true });
    }
  }
  const written = joinText(doc);

  let backup = null;
  if (before && !fs.existsSync(file + BACKUP_SUFFIX)) {
    fs.writeFileSync(file + BACKUP_SUFFIX, before);
    backup = file + BACKUP_SUFFIX;
  }
  fs.writeFileSync(file, written);
  fs.mkdirSync(path.dirname(recordFile), { recursive: true });
  fs.writeFileSync(recordFile, `${JSON.stringify({
    schema: 1,
    file,
    at: new Date().toISOString(),
    original: before ? before.toString('base64') : null,
    originalSha256: before ? sha256(before) : null,
    writtenSha256: sha256(written),
    backup,
    changes,
  }, null, 2)}\n`);
  return { changed: true };
}

// Undoes enableKeepAlive. Returns { result, message, notify }:
//   nothing         no record: Guaardvark changed nothing
//   restored        the file was as Guaardvark left it; put back exactly
//   lines-removed   the person edited the file since; only Guaardvark's lines went
//   partial         some of Guaardvark's lines now hold the person's values; kept
//   missing         the file is gone; nothing written
// notify is true when the person should be told what happened.
function undoKeepAlive(userProfile, recordFile) {
  const rec = readRecord(recordFile);
  if (!rec) return { result: 'nothing', notify: false };
  const file = configPath(userProfile);
  const current = readBytes(file);
  const original = rec.original ? Buffer.from(rec.original, 'base64') : null;
  const done = (out) => {
    fs.rmSync(recordFile, { force: true });
    return out;
  };
  const dropOwnBackup = () => {
    if (!rec.backup) return;
    const b = readBytes(rec.backup);
    if (b && original && sha256(b) === sha256(original)) fs.rmSync(rec.backup, { force: true });
  };

  if (!current) {
    const kept = rec.backup && fs.existsSync(rec.backup);
    return done({
      result: 'missing',
      notify: true,
      message: `${file} was deleted after Guaardvark turned on keep-alive, so there was nothing to undo.${kept ? ` Your file from before that is kept as ${rec.backup}.` : ''}`,
    });
  }

  if (sha256(current) === rec.writtenSha256) {
    if (original) fs.writeFileSync(file, original);
    else fs.rmSync(file, { force: true });
    dropOwnBackup();
    return done({ result: 'restored', notify: false });
  }

  const doc = splitText(current);
  const keptByYou = [];
  for (const c of rec.changes) {
    const hit = findKey(doc.lines, c.section, c.key);
    if (!hit) continue;
    if (hit.value !== c.value) {
      keptByYou.push(`${c.key}=${hit.value}`);
      continue;
    }
    if (c.previous === null) doc.lines.splice(hit.index, 1);
    else doc.lines[hit.index] = `${c.key}=${c.previous}`;
  }
  for (const c of rec.changes.filter((x) => x.sectionCreated)) {
    const secs = sectionsOf(doc.lines);
    const header = doc.lines.findIndex((l, i) => SECTION.test(l) && secs[i] === c.section);
    if (header < 0) continue;
    let end = header + 1;
    while (end < doc.lines.length && !SECTION.test(doc.lines[end])) end += 1;
    if (doc.lines.slice(header + 1, end).every((l) => !l.trim())) {
      doc.lines.splice(header, end - header);
      if (header > 0 && header === doc.lines.length && !doc.lines[header - 1].trim()) doc.lines.splice(header - 1, 1);
    }
  }
  const next = doc.lines.some((l) => l.trim()) ? joinText(doc) : null;
  if (next) fs.writeFileSync(file, next);
  else if (!original) fs.rmSync(file, { force: true });
  else fs.writeFileSync(file, next || Buffer.alloc(0));

  if (keptByYou.length) {
    return done({
      result: 'partial',
      notify: true,
      message: `You changed ${keptByYou.join(' and ')} in ${file} after Guaardvark turned on keep-alive, so Guaardvark left ${keptByYou.length > 1 ? 'them' : 'it'} as you set ${keptByYou.length > 1 ? 'them' : 'it'}. Everything else Guaardvark added is removed.${rec.backup && fs.existsSync(rec.backup) ? ` Your file from before keep-alive is kept as ${rec.backup}.` : ''}`,
    });
  }
  dropOwnBackup();
  return done({ result: 'lines-removed', notify: false });
}

module.exports = { configPath, isKeepAlive, enableKeepAlive, undoKeepAlive, BACKUP_SUFFIX };
