/**
 * Wake word detection utility for ContinuousVoiceChat.
 *
 * Checks Whisper transcription text for wake phrases like "Hey Guaardvark".
 * Uses fuzzy matching to handle common Whisper mis-transcriptions of
 * unusual proper nouns (e.g. "guard vark", "guad vark", "guardvark").
 */

/**
 * Compute Levenshtein distance between two strings.
 */
function levenshteinDistance(a, b) {
  const m = a.length;
  const n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));

  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1];
      } else {
        dp[i][j] = 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
      }
    }
  }

  return dp[m][n];
}

/**
 * Generate common Whisper mis-transcription variants for a name.
 */
function generateFuzzyVariants(name) {
  const variants = new Set([name]);

  // Known variants for "guaardvark"
  if (name === 'guaardvark') {
    variants.add('guard vark');
    variants.add('guardvark');
    variants.add('guad vark');
    variants.add('guaard vark');
    variants.add('guard bark');
    variants.add('guard dark');
    variants.add('guar dark');
    variants.add('guadvark');
    variants.add('gard vark');
    variants.add('godvark');
    variants.add('god vark');
  }

  // Generic: collapse doubled letters
  const noDoubles = name.replace(/(.)\1/g, '$1');
  if (noDoubles !== name) {
    variants.add(noDoubles);
  }

  // Split at mid-point (common for compound-looking words)
  if (name.length > 5) {
    const mid = Math.floor(name.length / 2);
    variants.add(name.slice(0, mid) + ' ' + name.slice(mid));
  }

  // Remove spaces (if name has them)
  const noSpaces = name.replace(/\s+/g, '');
  if (noSpaces !== name) {
    variants.add(noSpaces);
  }

  return Array.from(variants);
}

/**
 * Check if a text window fuzzy-matches a phrase using Levenshtein similarity.
 * Returns the best match position or -1.
 */
function fuzzyIndexOf(text, phrase, threshold) {
  const phraseLen = phrase.length;
  if (phraseLen === 0) return -1;

  let bestPos = -1;
  let bestSimilarity = 0;

  // Slide a window across the text, checking windows of phraseLen +/- 2 chars
  for (let windowSize = Math.max(1, phraseLen - 2); windowSize <= phraseLen + 2; windowSize++) {
    for (let i = 0; i <= text.length - windowSize; i++) {
      const window = text.slice(i, i + windowSize);
      const distance = levenshteinDistance(window, phrase);
      const similarity = 1 - (distance / Math.max(window.length, phraseLen));
      if (similarity >= threshold && similarity > bestSimilarity) {
        bestSimilarity = similarity;
        bestPos = i;
      }
    }
  }

  return bestPos;
}

/**
 * Check if transcription contains a wake phrase.
 *
 * @param {string} transcription - The Whisper transcription text
 * @param {string} systemName - The configured system name (e.g. "Guaardvark")
 * @returns {{ detected: boolean, remainder: string, matchedPhrase?: string }}
 */
export function checkForWakeWord(transcription, systemName) {
  if (!transcription || !systemName) {
    return { detected: false, remainder: transcription || '' };
  }

  // Strip common Whisper punctuation that breaks phrase matching
  // e.g. "Hey, Ducky." → "hey ducky", "Hey Ducky!" → "hey ducky"
  const text = transcription
    .toLowerCase()
    .replace(/[,.:;!?'"()[\]{}]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  const name = systemName.toLowerCase().trim();

  if (!text || !name) {
    return { detected: false, remainder: transcription };
  }

  // Build all wake phrases to check
  const prefixes = ['hey', 'ok', 'hi', 'hello'];
  const nameVariants = generateFuzzyVariants(name);

  // Build prefixed phrases (always included)
  const prefixedPhrases = [];
  for (const variant of nameVariants) {
    for (const prefix of prefixes) {
      prefixedPhrases.push(`${prefix} ${variant}`);
    }
  }

  // For short names (<=5 chars), bare name matching causes too many false positives
  // (e.g., "ducky" in "rubber ducky"). Require a prefix for short names.
  const barePhrases = [];
  if (name.length > 5) {
    for (const variant of nameVariants) {
      barePhrases.push(variant);
    }
  }

  const allPhrases = [...prefixedPhrases, ...barePhrases];

  // Sort by length descending to match longest (most specific) first
  allPhrases.sort((a, b) => b.length - a.length);

  // Phase 1: Exact substring match
  for (const phrase of allPhrases) {
    const idx = text.indexOf(phrase);
    if (idx !== -1) {
      const remainder = text.slice(idx + phrase.length).trim();
      return { detected: true, remainder, matchedPhrase: phrase };
    }
  }

  // Phase 2: Fuzzy match with Levenshtein (0.7 threshold)
  for (const phrase of allPhrases) {
    // Only fuzzy-match phrases of 4+ chars to avoid false positives
    if (phrase.length < 4) continue;

    const pos = fuzzyIndexOf(text, phrase, 0.7);
    if (pos !== -1) {
      // Estimate the end of the matched region
      const matchEnd = Math.min(text.length, pos + phrase.length + 2);
      const remainder = text.slice(matchEnd).trim();
      return { detected: true, remainder, matchedPhrase: phrase };
    }
  }

  return { detected: false, remainder: text };
}

export default checkForWakeWord;
