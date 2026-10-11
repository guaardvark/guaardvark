import { describe, expect, it } from 'vitest';
import {
  buildFinalPrompts,
  clampQuantity,
  ignoresNegativeAndAnatomy,
  imageModelOption,
  isZimageModel,
  modelFamily,
  modelInstallNeed,
  qualityPresetsForModel,
  resolveQualityPreset,
} from './batchImageSettings';

describe('modelFamily', () => {
  it('classifies the catalog ids', () => {
    expect(modelFamily('auto')).toBe('auto');
    expect(modelFamily('zimage-turbo')).toBe('zimage');
    expect(modelFamily('flux-dev')).toBe('flux');
    expect(modelFamily('krea2-turbo')).toBe('krea-turbo');
    expect(modelFamily('krea2-raw')).toBe('krea-raw');
    expect(modelFamily('sd-xl')).toBe('sdxl');
    expect(modelFamily('sdxl-turbo')).toBe('sdxl-turbo');
    expect(modelFamily('realistic-vision')).toBe('sd');
    expect(modelFamily('sana-sprint')).toBe('sana');
    expect(modelFamily('sana-sprint-0.6b')).toBe('sana');
  });

  it('resolves Qwen-Image 2.1 ids, shipped and user, and leaves the editing model alone', () => {
    expect(modelFamily('qwen-image-21')).toBe('qwen-image-21');
    expect(modelFamily('user-qwen-image-21-foo')).toBe('qwen-image-21');
    expect(modelFamily('user-qwen-image-21-ft-fp8-xl')).toBe('qwen-image-21');
    expect(modelFamily('qwen-image-edit')).toBe('sd');
  });

  it('does not treat auto as Z-Image (the router may pick SDXL)', () => {
    expect(isZimageModel('auto')).toBe(false);
    expect(isZimageModel('zimage-turbo')).toBe(true);
  });
});

describe('ignoresNegativeAndAnatomy', () => {
  it('is true on the guidance-0 path (Z-Image, Krea 2 Turbo)', () => {
    expect(ignoresNegativeAndAnatomy('zimage-turbo')).toBe(true);
    expect(ignoresNegativeAndAnatomy('krea2-turbo')).toBe(true);
  });

  it('is true for Sana Sprint, whose pipeline takes no negative prompt', () => {
    expect(ignoresNegativeAndAnatomy('sana-sprint')).toBe(true);
  });

  it('is false for Krea Raw, auto, and CFG families', () => {
    expect(ignoresNegativeAndAnatomy('krea2-raw')).toBe(false);
    expect(ignoresNegativeAndAnatomy('auto')).toBe(false);
    expect(ignoresNegativeAndAnatomy('flux-dev')).toBe(false);
    expect(ignoresNegativeAndAnatomy('sd-xl')).toBe(false);
  });
});

describe('quality presets', () => {
  it('gives sdxl-turbo its own 1-4 step CFG-free presets', () => {
    const presets = qualityPresetsForModel('sdxl-turbo');
    expect(presets.every((p) => p.steps <= 4 && p.guidance === 0)).toBe(true);
    // SDXL Base presets would run at numbers the validator clamps away.
    expect(qualityPresetsForModel('sd-xl').some((p) => p.steps === 25)).toBe(true);
  });

  it('gives Qwen-Image 2.1 the official template recipe and nothing faster', () => {
    const presets = qualityPresetsForModel('qwen-image-21');
    expect(presets.map((p) => p.value)).toEqual(['standard', 'high']);
    expect(presets.every((p) => p.guidance === 1.0)).toBe(true);
    expect(presets.every((p) => p.steps >= 25)).toBe(true);
    expect(presets.find((p) => p.value === 'standard').steps).toBe(25);
    const high = presets.find((p) => p.value === 'high').steps;
    expect(high).toBeGreaterThanOrEqual(40);
    expect(high).toBeLessThanOrEqual(50);
    expect(resolveQualityPreset('user-qwen-image-21-foo', 'flux-ultra')).toBe('standard');
    expect(qualityPresetsForModel('user-qwen-image-21-foo')).toBe(presets);
  });

  it('keeps the current preset when the new family offers it', () => {
    expect(resolveQualityPreset('sd-xl', 'fast')).toBe('fast');
    expect(resolveQualityPreset('zimage-turbo', 'standard')).toBe('standard');
  });

  it('falls back to the family default for an out-of-range preset', () => {
    expect(resolveQualityPreset('flux-dev', 'standard')).toBe('flux-quality');
    expect(resolveQualityPreset('krea2-raw', 'fast')).toBe('high');
    expect(resolveQualityPreset('zimage-turbo', 'flux-ultra')).toBe('standard');
    expect(resolveQualityPreset('sd-xl', undefined)).toBe('standard');
  });

  it('every family default exists in its own list', () => {
    for (const model of ['auto', 'zimage-turbo', 'flux-dev', 'krea2-turbo', 'krea2-raw', 'sd-xl', 'sdxl-turbo', 'realistic-vision', 'sana-sprint', 'qwen-image-21']) {
      const def = resolveQualityPreset(model, 'not-a-preset');
      expect(qualityPresetsForModel(model).map((p) => p.value)).toContain(def);
    }
  });
});

describe('clampQuantity', () => {
  it('clamps to 1..100', () => {
    expect(clampQuantity('0')).toBe(1);
    expect(clampQuantity('')).toBe(1);
    expect(clampQuantity('7')).toBe(7);
    expect(clampQuantity('250')).toBe(100);
  });
});

describe('buildFinalPrompts', () => {
  it('single mode: whole text is one prompt with Look & Feel appended', () => {
    expect(
      buildFinalPrompts({ inputMode: 'single', batchItems: 'a fox\nin snow', lookAndFeel: ' film grain ', quantity: 1 }),
    ).toEqual(['a fox\nin snow, film grain']);
  });

  it('bulk mode: one per non-empty line, repeated by quantity, no copy counter', () => {
    expect(
      buildFinalPrompts({ inputMode: 'bulk', batchItems: 'a\n\n b \n', lookAndFeel: '', quantity: 2 }),
    ).toEqual(['a', 'a', 'b', 'b']);
  });

  it('returns [] for empty input so callers can refuse to start', () => {
    expect(buildFinalPrompts({ inputMode: 'single', batchItems: '  ', lookAndFeel: 'x', quantity: 3 })).toEqual([]);
    expect(buildFinalPrompts({ inputMode: 'bulk', batchItems: '\n\n', lookAndFeel: '', quantity: 1 })).toEqual([]);
  });
});

describe('model install state', () => {
  const row = (id, extra) => ({ id, label: id, name: id, size_gb: 16, ...extra });
  const AUTO = { value: 'auto', label: 'Auto' };

  it('describes a missing model as needing Install, not as a first-use download', () => {
    const opt = imageModelOption(row('zimage-turbo', { is_downloaded: false, availability: 'downloadable' }));
    expect(opt.installed).toBe(false);
    expect(opt.description).toContain('not installed');
    expect(opt.description).toContain('Install downloads ~16 GB');
    expect(opt.description).not.toContain('first use');
    expect(imageModelOption(row('sd-xl', { is_downloaded: true })).installed).toBe(true);
  });

  it('blocks Generate for a picked model that is not installed', () => {
    const options = [AUTO, imageModelOption(row('krea2-turbo', { is_downloaded: false }))];
    const need = modelInstallNeed(options, 'krea2-turbo');
    expect(need.blocks).toBe(true);
    expect(need.option.value).toBe('krea2-turbo');
  });

  it('never blocks Auto, and points it at the recommended model when nothing is installed', () => {
    const none = [
      AUTO,
      imageModelOption(row('sd-xl', { is_downloaded: false })),
      imageModelOption(row('zimage-turbo', { is_downloaded: false, recommended: true })),
    ];
    expect(modelInstallNeed(none, 'auto')).toEqual({ option: none[2], blocks: false });
    const some = [AUTO, imageModelOption(row('sd-xl', { is_downloaded: true }))];
    expect(modelInstallNeed(some, 'auto')).toBeNull();
    expect(modelInstallNeed(some, 'sd-xl')).toBeNull();
  });
});
