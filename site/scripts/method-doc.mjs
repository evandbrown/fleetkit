// Vite plugin: `import { html, headings } from 'virtual:method'` gives docs/method.md rendered to HTML
// at build time, so the Markdown renderer never ships to the browser.
import { readFileSync } from 'node:fs';
import { posix, resolve } from 'node:path';
import { Marked } from 'marked';

const ID = 'virtual:method';
const RESOLVED = '\0' + ID;
const REPO = 'https://github.com/evandbrown/fleetkit/blob/main/';

export function slug(text) {
  return text
    .replace(/<[^>]*>/g, '')
    .toLowerCase()
    .replace(/&[a-z]+;/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

/** Where a link in docs/method.md should point on the site. */
export function siteHref(href) {
  if (/^[a-z]+:/i.test(href)) return href;                 // absolute URL
  if (href.startsWith('#')) return `#/method/${href.slice(1)}`;
  const [path, frag] = href.split('#');
  const repoPath = posix.normalize(posix.join('docs', path));
  if (repoPath === 'docs/method.md') return frag ? `#/method/${frag}` : '#/method';
  return REPO + repoPath + (frag ? `#${frag}` : '');
}

export function renderMethod(markdown) {
  const headings = [];
  const marked = new Marked({ gfm: true });
  marked.use({
    renderer: {
      heading({ tokens, depth }) {
        const inner = this.parser.parseInline(tokens);
        const id = slug(inner);
        headings.push({ depth, id, text: inner.replace(/<[^>]*>/g, '') });
        return `<h${depth} id="${id}">${inner}</h${depth}>\n`;
      },
      link({ href, title, tokens }) {
        const inner = this.parser.parseInline(tokens);
        const to = siteHref(href);
        const external = /^https?:/.test(to);
        const t = title ? ` title="${title}"` : '';
        const rel = external ? ' rel="noopener"' : '';
        return `<a href="${to}"${t}${rel}>${inner}</a>`;
      },
      table(token) {
        // Wide tables scroll inside their own box on a phone instead of widening the page.
        const header = token.header.map((c) => `<th>${this.parser.parseInline(c.tokens)}</th>`).join('');
        const rows = token.rows
          .map((r) => `<tr>${r.map((c) => `<td>${this.parser.parseInline(c.tokens)}</td>`).join('')}</tr>`)
          .join('\n');
        return `<div class="table-wrap"><table><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>\n`;
      },
    },
  });
  const html = marked.parse(markdown);
  return { html, headings };
}

export function methodDoc(file = '../docs/method.md') {
  let path = '';
  return {
    name: 'fleetkit:method-doc',
    configResolved(config) {
      path = resolve(config.root, file);
    },
    resolveId(id) {
      return id === ID ? RESOLVED : null;
    },
    load(id) {
      if (id !== RESOLVED) return null;
      this.addWatchFile(path);
      const { html, headings } = renderMethod(readFileSync(path, 'utf8'));
      return `export const html = ${JSON.stringify(html)};\nexport const headings = ${JSON.stringify(headings)};\n`;
    },
  };
}
