// Keeps synthetic test data out of the published site, and serves the fixtures in development.
// See DATA.md, "Synthetic data".
import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, normalize, resolve, sep } from 'node:path';

const SYNTHETIC = /"synthetic"\s*:\s*true/;

/** Every JSON file under `dir` that marks itself synthetic, as paths relative to `dir`. */
export function findSynthetic(dir) {
  const found = [];
  if (!existsSync(dir)) return found;
  const walk = (d, rel) => {
    for (const name of readdirSync(d)) {
      const p = join(d, name);
      const r = rel ? `${rel}/${name}` : name;
      if (statSync(p).isDirectory()) walk(p, r);
      else if (name.endsWith('.json') && SYNTHETIC.test(readFileSync(p, 'utf8'))) found.push(r);
    }
  };
  walk(dir, '');
  return found;
}

/** Vite plugin: `vite build` fails if the published dataset contains synthetic data. */
export function refuseSyntheticData() {
  let publicDir = '';
  let command = 'serve';
  return {
    name: 'fleetkit:refuse-synthetic-data',
    configResolved(config) {
      publicDir = config.publicDir;
      command = config.command;
    },
    buildStart() {
      if (command !== 'build') return;
      const found = findSynthetic(join(publicDir, 'data'));
      if (found.length) {
        this.error(
          `public/data contains synthetic test data (${found.join(', ')}). ` +
            'Synthetic campaigns belong in tests/fixtures/data only.',
        );
      }
    },
  };
}

const TYPES = { '.json': 'application/json', '.webp': 'image/webp' };

/** Vite plugin for `vite --mode fixtures`: serves tests/fixtures/data at ./data/ in the dev server only. */
export function fixturesData(dataDir) {
  const root = resolve(dataDir);
  return {
    name: 'fleetkit:fixtures-data',
    apply: 'serve',
    configureServer(server) {
      server.config.logger.info(`  serving ./data/ from ${root} (synthetic fixtures; development only)`);
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0];
        const at = url.indexOf('/data/');
        if (at < 0) return next();
        const file = normalize(join(root, decodeURIComponent(url.slice(at + '/data/'.length))));
        if (!file.startsWith(root + sep) || !existsSync(file) || statSync(file).isDirectory()) {
          res.statusCode = 404;
          return res.end('not in the fixtures');
        }
        res.setHeader('Content-Type', TYPES[extname(file)] ?? 'application/octet-stream');
        createReadStream(file).pipe(res);
      });
    },
  };
}
