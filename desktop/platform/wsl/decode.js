// Text from wsl.exe and other Windows tools.
//
// wsl.exe writes its own messages (help, errors, `--list`, `--status`) as
// UTF-16LE without a byte order mark when its output is redirected. Setting
// WSL_UTF8=1 in its environment switches that to UTF-8 on current WSL
// releases, but older ones ignore the variable, so the bytes are checked
// rather than trusted. Output of Linux programs run through `wsl.exe -d` is
// passed through as the program wrote it (UTF-8).

function looksUtf16le(buf) {
  if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) return true;
  if (buf.length < 4) return buf.length === 2 && buf[1] === 0;
  // ASCII-range text in UTF-16LE has a zero in most odd positions.
  let zeros = 0;
  let pairs = 0;
  const limit = Math.min(buf.length - 1, 4096);
  for (let i = 1; i <= limit; i += 2) {
    pairs += 1;
    if (buf[i] === 0) zeros += 1;
  }
  return zeros / pairs > 0.6;
}

function decode(buf) {
  if (buf == null) return '';
  if (typeof buf === 'string') return clean(buf);
  let text;
  if (looksUtf16le(buf)) {
    const start = buf[0] === 0xff && buf[1] === 0xfe ? 2 : 0;
    text = buf.subarray(start, start + ((buf.length - start) & ~1)).toString('utf16le');
  } else {
    text = buf.toString('utf8');
  }
  return clean(text);
}

function clean(text) {
  return text.replace(/^﻿/, '').replace(/\u0000/g, '').replace(/\r\n?/g, '\n');
}

function lines(text) {
  return text.split('\n').map((l) => l.trimEnd()).filter((l) => l.trim() !== '');
}

module.exports = { decode, lines, looksUtf16le };
