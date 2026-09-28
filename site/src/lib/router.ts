// A tiny hash router. Hash routes keep every URL relative, so the site works under /fleetkit/ with no
// server rewrites.
//   #/  #/results                                          Results, on the featured campaign
//   #/results/:campaign                                    Results, on one campaign
//   #/results/:campaign/runs/:run?density=8                one run, optionally its trials at one density
//   #/results/:campaign/runs/:run/trials/:trial?microvm=3  one trial, optionally one microVM
//   #/results/compare?specs=<campaign>/<spec>,...          specs from any campaigns, side by side
//   #/builder?from=campaign:<c> | run:<c>/<run>            the experiment builder (DATA.md, "Builder handoff")
//   #/about
// Older addresses (#/campaigns/..., #/compare, #/method/...) still open their page, and the address bar is rewritten
// to the new one (resolve, below).

export type Route =
  | { name: 'results'; campaign: string | null }
  | { name: 'run'; campaign: string; run: string; density: number | null }
  | { name: 'trial'; campaign: string; run: string; trial: string; microvm: number | null }
  | { name: 'compare'; specs: string[] | null }
  | { name: 'builder'; from: string | null }
  | { name: 'about' }
  | { name: 'not_found'; path: string };

/** Anything href() can link to. */
export type Link = Route;

const ID = '[a-z0-9][a-z0-9-]*';
const SEG = `(${ID})`;
const SPEC_REF = new RegExp(`^${ID}/${ID}$`);
const FROM = new RegExp(`^(?:campaign:${ID}|run:${ID}/${ID})$`);

type Make = (m: string[], q: URLSearchParams) => Route;

const run: Make = (m, q) => ({ name: 'run', campaign: m[1], run: m[2], density: posInt(q.get('density')) });
const trial: Make = (m, q) => ({
  name: 'trial',
  campaign: m[1],
  run: m[2],
  trial: m[3],
  microvm: posInt(q.get('microvm')),
});
const compare: Make = (_, q) => {
  // No specs parameter: the page picks its defaults. An empty one: the reader unticked every spec.
  const raw = q.get('specs');
  return { name: 'compare', specs: raw === null ? null : [...new Set(raw.split(',').filter((s) => SPEC_REF.test(s)))] };
};

// Tried in order: compare comes before a campaign id, so #/results/compare is never read as a campaign.
const PATTERNS: [RegExp, Make][] = [
  [/^\/?$/, () => ({ name: 'results', campaign: null })],
  [/^\/results\/?$/, () => ({ name: 'results', campaign: null })],
  [/^\/results\/compare\/?$/, compare],
  [new RegExp(`^/results/${SEG}/?$`), (m) => ({ name: 'results', campaign: m[1] })],
  [new RegExp(`^/results/${SEG}/runs/${SEG}/?$`), run],
  [new RegExp(`^/results/${SEG}/runs/${SEG}/trials/${SEG}/?$`), trial],
  [
    /^\/builder\/?$/,
    (_, q) => {
      const from = q.get('from');
      return { name: 'builder', from: from && FROM.test(from) ? from : null };
    },
  ],
  [/^\/about\/?$/, () => ({ name: 'about' })],
];

// The addresses of the first version of the site. Each opens the page that replaced it.
const LEGACY: [RegExp, Make][] = [
  [/^\/campaigns\/?$/, () => ({ name: 'results', campaign: null })],
  [new RegExp(`^/campaigns/${SEG}/?$`), (m) => ({ name: 'results', campaign: m[1] })],
  [new RegExp(`^/campaigns/${SEG}/runs/${SEG}/?$`), run],
  [new RegExp(`^/campaigns/${SEG}/runs/${SEG}/trials/${SEG}/?$`), trial],
  [/^\/compare\/?$/, compare],
  [/^\/method(?:\/[a-z0-9-]+)?\/?$/, () => ({ name: 'about' })],
];

function posInt(s: string | null): number | null {
  if (s === null || !/^[1-9]\d*$/.test(s)) return null;
  return Number(s);
}

function match(table: [RegExp, Make][], path: string, q: URLSearchParams): Route | null {
  for (const [re, make] of table) {
    const m = re.exec(path);
    if (m) return make(m, q);
  }
  return null;
}

/**
 * The route for a location hash, and the address to show instead when the hash is an older one (null when it's
 * current). The caller rewrites the address bar without adding a history entry.
 */
export function resolve(hash: string): { route: Route; redirect: string | null } {
  const raw = hash.replace(/^#/, '');
  const [path, query = ''] = raw.split('?');
  const q = new URLSearchParams(query);
  const current = match(PATTERNS, path, q);
  if (current) return { route: current, redirect: null };
  const legacy = match(LEGACY, path, q);
  if (legacy) return { route: legacy, redirect: href(legacy) };
  return { route: { name: 'not_found', path: raw }, redirect: null };
}

export function parse(hash: string): Route {
  return resolve(hash).route;
}

export function href(r: Link): string {
  switch (r.name) {
    case 'results':
      return r.campaign === null ? '#/results' : `#/results/${r.campaign}`;
    case 'run':
      return `#/results/${r.campaign}/runs/${r.run}${r.density ? `?density=${r.density}` : ''}`;
    case 'trial':
      return `#/results/${r.campaign}/runs/${r.run}/trials/${r.trial}${r.microvm ? `?microvm=${r.microvm}` : ''}`;
    case 'compare':
      return r.specs === null ? '#/results/compare' : `#/results/compare?specs=${r.specs.join(',')}`;
    case 'builder':
      return r.from ? `#/builder?from=${r.from}` : '#/builder';
    case 'about':
      return '#/about';
    case 'not_found':
      return `#${r.path}`;
  }
}

/** Which item of the navigation a route belongs to. */
export function area(r: Route): 'results' | 'builder' | 'about' | null {
  switch (r.name) {
    case 'results':
    case 'run':
    case 'trial':
    case 'compare':
      return 'results';
    case 'builder':
    case 'about':
      return r.name;
    default:
      return null;
  }
}
