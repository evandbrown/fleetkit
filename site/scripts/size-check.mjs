// Enforces the size budget (gzip) on the built site. Runs after `vite build`.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { gzipSync } from 'node:zlib';

const dist = process.argv[2] ?? 'dist';
const KB = 1024;
const BUDGET = {
  code: 70 * KB,        // all JS and CSS
  index: 5 * KB,        // data/index.json
  doc: 25 * KB,         // each campaign, run or trial document
  data: 2 * 1024 * KB,  // the whole dataset, images included
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
const problems = [];
let code = 0;
let data = 0;
for (const p of files) {
  const rel = relative(dist, p).split('\\').join('/');
  const size = gz(p);
  if (/\.(js|css)$/.test(rel)) code += size;
  if (rel.startsWith('data/')) {
    data += size;
    if (rel === 'data/index.json' && size > BUDGET.index) problems.push(`${rel}: ${size} B > ${BUDGET.index} B`);
    else if (rel.endsWith('.json') && rel !== 'data/index.json' && size > BUDGET.doc) {
      problems.push(`${rel}: ${size} B > ${BUDGET.doc} B`);
    }
  }
}
if (code > BUDGET.code) problems.push(`JS + CSS: ${code} B > ${BUDGET.code} B`);
if (data > BUDGET.data) problems.push(`dataset: ${data} B > ${BUDGET.data} B`);

console.log(`size-check: JS + CSS ${(code / KB).toFixed(1)} KB gz (budget 70), dataset ${(data / KB).toFixed(1)} KB gz (budget 2,048)`);
if (problems.length) {
  console.error('size-check failed:\n  ' + problems.join('\n  '));
  process.exit(1);
}
