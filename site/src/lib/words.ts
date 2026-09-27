// Retired words (D56). They never appear in field names, ids, file names, values, code or UI.
// The list itself has to name them, so it sits between the markers the word test skips.

// legacy-names:start
export const RETIRED = ['level', 'repeat', 'factor', 'condition', 'session', 'VMM'] as const;
const RETIRED_RE = /\b(levels?|repeats?|factors?|conditions?|sessions?|vmms?)\b/i;
// legacy-names:end

/** Splits identifiers into words, so snake_case, camelCase and kebab-case spellings are all caught. */
export function words(text: string): string {
  return text.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_\-./]/g, ' ');
}

export function retiredWordIn(text: string): string | null {
  const m = RETIRED_RE.exec(words(text));
  return m ? m[1] : null;
}

/** Every key or string value in a JSON value that uses a retired word, as "path: word". */
export function retiredWordsInJson(value: unknown, path = '$'): string[] {
  const hits: string[] = [];
  if (typeof value === 'string') {
    const w = retiredWordIn(value);
    if (w) hits.push(`${path}: "${w}"`);
  } else if (Array.isArray(value)) {
    value.forEach((v, i) => hits.push(...retiredWordsInJson(v, `${path}[${i}]`)));
  } else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      const w = retiredWordIn(k);
      if (w) hits.push(`${path}.${k} (key): "${w}"`);
      hits.push(...retiredWordsInJson(v, `${path}.${k}`));
    }
  }
  return hits;
}

/** Removes the marked blocks that are allowed to name old words (Markdown and code comments). */
export function withoutLegacyBlocks(text: string): string {
  return text
    .replace(/<!-- legacy-names:start -->[\s\S]*?<!-- legacy-names:end -->/g, '')
    .replace(/\/\/ legacy-names:start[\s\S]*?\/\/ legacy-names:end/g, '');
}
