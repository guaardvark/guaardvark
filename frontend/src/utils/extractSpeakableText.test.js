/**
 * Tests for TTS text extraction.
 * Prevents regression: voice should only speak the conversational part,
 * not meta-commentary, reasoning, or technical output.
 */
import { describe, it, expect } from 'vitest';
import extractSpeakableText from './extractSpeakableText';

describe('extractSpeakableText', () => {
  describe('meta-commentary stripping', () => {
    it('strips "Note:" preamble and keeps conversational text', () => {
      const input =
        'Note: I was unable to verify this through a web search. Hello! I\'m doing well, thanks for asking.';
      const result = extractSpeakableText(input);
      expect(result).toContain('Hello');
      expect(result).toContain('doing well');
      expect(result).not.toContain('Note:');
      expect(result).not.toContain('unable to verify');
    });

    it('strips "Based on" preamble', () => {
      const input =
        'Based on the generated image, it seems that the user wants a chicken.\nHere is your cartoon chicken! I hope you like it.';
      const result = extractSpeakableText(input);
      expect(result).toContain('cartoon chicken');
      expect(result).not.toContain('Based on');
      expect(result).not.toContain('the user');
    });

    it('strips "Since the" preamble', () => {
      const input =
        'Since the image was generated successfully, I can proceed to provide a response.\nHere is your image!';
      const result = extractSpeakableText(input);
      expect(result).toContain('Here is your image');
      expect(result).not.toContain('Since the');
    });

    it('strips trailing meta-commentary', () => {
      const input =
        'Here is your chicken image! It has bright colors and a fun style.\nThis response acknowledges the user\'s creative request.';
      const result = extractSpeakableText(input);
      expect(result).toContain('chicken image');
      expect(result).not.toContain('This response acknowledges');
    });
  });

  describe('quoted speech extraction', () => {
    it('extracts quoted speech when surrounded by meta-commentary', () => {
      const input = `Based on the generated image, it seems that the user is interested in seeing a cartoon-style image of a chicken. Since the image was generated successfully, I can proceed to provide a response.

Here's a possible response:

\u201cHere is the generated image of a chicken in a cartoon style! It's a fun and colorful representation.\u201d

This response acknowledges the user's request.`;
      const result = extractSpeakableText(input);
      expect(result).toContain('chicken in a cartoon style');
      expect(result).not.toContain('Based on');
      expect(result).not.toContain('This response acknowledges');
    });
  });

  describe('technical artifact removal', () => {
    it('removes URLs', () => {
      const result = extractSpeakableText(
        'Here is the image: https://example.com/gen_abc123.png I hope you like it!'
      );
      expect(result).not.toContain('https://');
      expect(result).toContain('hope you like it');
    });

    it('removes file paths', () => {
      const result = extractSpeakableText(
        'Saved to /home/user/data/outputs/gen_img.png - enjoy!'
      );
      expect(result).not.toContain('/home/');
    });

    it('removes model names and seeds', () => {
      const result = extractSpeakableText(
        'Generated using stable-diffusion at 512x512 with seed 42. Here you go!'
      );
      expect(result).not.toContain('stable-diffusion');
      expect(result).not.toContain('seed');
    });
  });

  describe('length capping', () => {
    it('caps long text to 3 sentences', () => {
      const input =
        'First sentence here. Second sentence here. Third sentence here. Fourth sentence here. Fifth sentence here.';
      const result = extractSpeakableText(input);
      const sentenceCount = (result.match(/[.!?]/g) || []).length;
      expect(sentenceCount).toBeLessThanOrEqual(3);
    });
  });

  describe('fallbacks', () => {
    it('returns "Done." for empty input', () => {
      expect(extractSpeakableText('')).toBe('Done.');
      expect(extractSpeakableText(null)).toBe('Done.');
      expect(extractSpeakableText(undefined)).toBe('Done.');
    });

    it('returns "Here you go." when images were generated', () => {
      expect(extractSpeakableText('', [{ url: 'test.png' }])).toBe('Here you go.');
    });

    it('returns "Done." when all content is meta', () => {
      const input = 'Based on the analysis, it seems the user wants something.';
      const result = extractSpeakableText(input);
      // Should fall through to "Done." since everything is meta
      expect(result).toBe('Done.');
    });
  });
});
