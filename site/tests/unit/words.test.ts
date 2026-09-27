// D56: retired words never appear in code, identifiers, files, docs or UI. Marked legacy blocks may name them.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import { retiredWordIn, retiredWordsInJson, withoutLegacyBlocks } from '../../src/lib/words';

const SITE = join(__dirname, '../..');
// The one-off derivation reads the old run directory's names, so it is the translation layer and is skipped.
const SKIP = new Set(['tests/fixtures/derive_cap_baseline_1.py']);

function files(dir: string, exts: RegExp): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return files(p, exts);
    return exts.test(n) && !SKIP.has(relative(SITE, p)) ? [p] : [];
  });
}

describe('retired words', () => {
  // legacy-names:start
  it('catch identifiers in any case style', () => {
    expect(retiredWordIn('level_n')).toBe('level');
    expect(retiredWordIn('sessionId')).toBe('session');
    expect(retiredWordIn('cpu_vmm_usec')).toBe('vmm');
    expect(retiredWordIn('conditional density')).toBeNull();
  });
  // legacy-names:end

  const texts = [
    ...files(join(SITE, 'src'), /\.(ts|svelte|css)$/),
    ...files(join(SITE, 'scripts'), /\.mjs$/),
    ...files(join(SITE, 'tests'), /\.(ts|mjs|py)$/).filter((p) => !p.includes('/fixtures/data/')),
    join(SITE, 'DATA.md'),
    join(SITE, 'README.md'),
    join(SITE, 'index.html'),
  ];
  for (const file of texts) {
    it(`are absent from ${relative(SITE, file)}`, () => {
      const lines = withoutLegacyBlocks(readFileSync(file, 'utf8')).split('\n');
      // CSS's grid function is spelled like a retired word but isn't one; only style code may use it.
      const css = /\.(svelte|css)$/.test(file);
      const hits = lines.flatMap((l, i) => {
        const w = retiredWordIn(css ? l.replace(/\brepeat\(/g, '(') : l);
        return w ? [`${i + 1}: ${w}: ${l.trim()}`] : [];
      });
      expect(hits).toEqual([]);
    });
  }

  it('are absent from every fixture document', () => {
    const hits = files(join(SITE, 'tests/fixtures/data'), /\.json$/).flatMap((f) =>
      retiredWordsInJson(JSON.parse(readFileSync(f, 'utf8'))).map((h) => `${relative(SITE, f)} ${h}`),
    );
    expect(hits).toEqual([]);
  });
});
