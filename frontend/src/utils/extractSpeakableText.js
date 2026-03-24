/**
 * Extract only the speakable/conversational portion from an LLM response.
 * Strips meta-commentary, reasoning preambles, and technical output so TTS
 * reads only what a human would naturally say aloud.
 *
 * Extracted to a standalone module for testability.
 */

const META_PATTERNS = [
  /^(note|disclaimer|warning|caveat)\s*:/i,
  /^based on (the|my|this)/i,
  /^since (the|I|this|we)/i,
  /^it (seems|appears|looks like) (that |like )?(the user|you)/i,
  /^(here'?s|this is) a possible (response|answer|reply)/i,
  /^this response (acknowledges|provides|addresses|confirms)/i,
  /^I (was unable to|couldn'?t|cannot|can'?t) (verify|confirm|find|check)/i,
  /^(the user|they) (is|are|was|were|wants?|asked|requested|seems?)/i,
  /^(let me|I ('?ll|will|should|can)) (proceed|provide|give|offer)/i,
  /^(image url|size|model|seed|prompt|style|filename|resolution)\s*:/i,
  /^(in (summary|conclusion)|to (summarize|sum up))/i,
];

/**
 * Split text into sentence-level segments for classification.
 * Handles both newline-separated and single-line multi-sentence text.
 */
function splitIntoSegments(text) {
  // First split on newlines
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  const segments = [];
  for (const line of lines) {
    // If a line contains multiple sentences, split further so meta-preambles
    // within a line don't drag down the entire line.
    const sentences = line.match(/[^.!?]*[.!?]+/g);
    if (sentences && sentences.length > 1) {
      for (const s of sentences) {
        const trimmed = s.trim();
        if (trimmed) segments.push(trimmed);
      }
    } else {
      segments.push(line);
    }
  }
  return segments;
}

export default function extractSpeakableText(rawText, generatedImages) {
  if (!rawText || typeof rawText !== 'string') {
    return generatedImages?.length ? "Here you go." : "Done.";
  }

  let text = rawText;

  // 1. Strip technical artifacts (URLs, paths, model names, seeds, dimensions)
  text = text.replace(/https?:\/\/\S+/g, '');
  text = text.replace(/\/api\/\S+/g, '');
  text = text.replace(/\/home\/\S+/g, '');
  text = text.replace(/\b(runwayml|stable-diffusion|sd-1\.5|sdxl|gen_\w+\.png)\b/gi, '');
  text = text.replace(/\b\d+x\d+\s*(pixel)?\b/gi, '');
  text = text.replace(/\bseed[:\s]*\d+/gi, '');

  // 2. If there's a quoted speech block, extract it preferentially.
  const quotedSpeech = text.match(/["\u201c]([\s\S]{10,?})["\u201d]/);
  if (quotedSpeech) {
    const outsideQuote = text.replace(quotedSpeech[0], '').trim();
    const metaSignals = (outsideQuote.match(/\b(based on|it seems|the user|possible response|this response|I can proceed|since the|let me)\b/gi) || []).length;
    if (metaSignals >= 2) {
      text = quotedSpeech[1].trim();
    }
  }

  // 3. Split into sentences/segments and classify each as meta vs. speakable
  const segments = splitIntoSegments(text);

  const speakableSegments = [];
  let foundSpeakable = false;

  for (const segment of segments) {
    const isMeta = META_PATTERNS.some(p => p.test(segment));
    if (isMeta && !foundSpeakable) {
      continue; // Skip leading meta-commentary
    }
    if (!isMeta) {
      foundSpeakable = true;
    }
    if (foundSpeakable && !isMeta) {
      speakableSegments.push(segment);
    }
    // Once we've found speakable text, trailing meta is also dropped
  }

  // 4. Reassemble
  let ttsText = speakableSegments.join(' ').replace(/\s+/g, ' ').trim();

  // 5. Cap to 3 sentences max for natural speech
  const allSentences = ttsText.match(/[^.!?]+[.!?]+/g) || [ttsText];
  if (allSentences.length > 3) {
    ttsText = allSentences.slice(0, 3).join(' ').trim();
  }

  // 6. Fallback
  if (!ttsText || ttsText.length < 5) {
    ttsText = generatedImages?.length ? "Here you go." : "Done.";
  }

  return ttsText;
}
