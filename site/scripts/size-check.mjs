// Enforces the size budget (gzip) on the built site. Runs after `vite build`.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { gzipSync } from 'node:zlib';

const dist = process.argv[2] ?? 'dist';
const KB = 1024;
const BUDGET = {
  code: 70 * KB,        // the JS and CSS every page loads first (what index.html references)
  lazy: 40 * KB,        // each chunk loaded only when needed (the experiment builder)
  index: 5 * KB,        // data/index.json
  doc: 80 * KB,         // each campaign, run or trial document; a metal trial of 192 microVMs is about 70 KB
  data: 16 * 1024 * KB, // the whole dataset, images included; pages load it lazily, one document at a time
};

const files = [];
const walk = (d) => {
  for (const n of readdirSync(d)) {
    const p = join(d, n);
    statSync(p).isDirectory() ? walk(p) : files.push(p);
  }
};
if (!existsSync(dist)) {
  console.error(`size-check: ${dist} does not exist; run vite build first`);
  process.exit(1);
}
walk(dist);

const gz = (p) => gzipSync(readFileSync(p)).length;
const html = readFileSync(join(dist, 'index.html'), 'utf8');
const initial = new Set([...html.matchAll(/(?:src|href)="\.\/(assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]));
const problems = [];
let code = 0;
let lazy = 0;
let data = 0;
for (const p of files) {
  const rel = relative(dist, p).split('\\').join('/');
  const size = gz(p);
  if (/\.(js|css)$/.test(rel)) {
    if (initial.has(rel)) code += size;
    else {
      lazy += size;
      if (size > BUDGET.lazy) problems.push(`${rel}: ${size} B > ${BUDGET.lazy} B (a lazy chunk)`);
    }
  }
  if (rel.startsWith('data/')) {
    data += size;
    if (rel === 'data/index.json' && size > BUDGET.index) problems.push(`${rel}: ${size} B > ${BUDGET.index} B`);
    else if (rel.endsWith('.json') && rel !== 'data/index.json' && size > BUDGET.doc) {
      problems.push(`${rel}: ${size} B > ${BUDGET.doc} B`);
    }
  }
}
if (!initial.size) problems.push('index.html references no JS or CSS');
if (code > BUDGET.code) problems.push(`JS + CSS loaded first: ${code} B > ${BUDGET.code} B`);
if (data > BUDGET.data) problems.push(`dataset: ${data} B > ${BUDGET.data} B`);

console.log(
  `size-check: JS + CSS loaded first ${(code / KB).toFixed(1)} KB gz (budget 70), lazy chunks ${(lazy / KB).toFixed(1)} KB gz ` +
    `(budget 40 each), dataset ${(data / KB).toFixed(1)} KB gz (budget 16,384)`,
);
if (problems.length) {
  console.error('size-check failed:\n  ' + problems.join('\n  '));
  process.exit(1);
}
