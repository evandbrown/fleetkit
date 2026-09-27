// Serves the built site under a path prefix, as production does (evan.mx/fleetkit/).
//   node scripts/serve.mjs --dir dist --base /fleetkit/ [--data tests/fixtures/data] [--port 4173]
// --data serves another dataset at <base>data/ (the fixtures, for end-to-end tests) without copying it into dist.
import { createServer } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve, sep } from 'node:path';

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]]] : acc), []),
);
const dir = resolve(args.dir ?? 'dist');
const base = args.base ?? '/fleetkit/';
const data = args.data ? resolve(args.data) : null;
const port = Number(args.port ?? 4173);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.png': 'image/png',
};

function send(res, root, rel) {
  const file = normalize(join(root, rel || 'index.html'));
  if (!(file === root || file.startsWith(root + sep)) || !existsSync(file) || statSync(file).isDirectory()) {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    return res.end('not found');
  }
  res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
}

createServer((req, res) => {
  const url = decodeURIComponent((req.url ?? '/').split('?')[0]);
  if (url === base.replace(/\/$/, '')) {
    res.writeHead(301, { Location: base });
    return res.end();
  }
  if (!url.startsWith(base)) {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    return res.end(`outside ${base}`);
  }
  const rel = url.slice(base.length);
  if (data && rel.startsWith('data/')) return send(res, data, rel.slice('data/'.length));
  return send(res, dir, rel);
}).listen(port, () => {
  console.log(`serving ${dir} at http://localhost:${port}${base}` + (data ? ` with data from ${data}` : ''));
});
