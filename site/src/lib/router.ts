// A tiny hash router. Hash routes keep every URL relative, so the site works under /fleetkit/ with no
// server rewrites.
//   #/                                              opens the most recent campaign
//   #/campaigns                                     every campaign
//   #/campaigns/:campaign                           one campaign
//   #/campaigns/:campaign/runs/:run?density=8       one run, optionally its trials at one density
//   #/campaigns/:campaign/runs/:run/trials/:trial?microvm=3
//   #/builder  #/method  #/method/:anchor  #/about

export type Route =
  | { name: 'home' }
  | { name: 'campaigns' }
  | { name: 'campaign'; campaign: string }
  | { name: 'run'; campaign: string; run: string; density: number | null }
  | { name: 'trial'; campaign: string; run: string; trial: string; microvm: number | null }
  | { name: 'builder' }
  | { name: 'method'; anchor: string | null }
  | { name: 'about' }
  | { name: 'not_found'; path: string };

const SEG = '([a-z0-9][a-z0-9-]*)';
const PATTERNS: [RegExp, (m: string[], q: URLSearchParams) => Route][] = [
  [/^\/?$/, () => ({ name: 'home' })],
  [/^\/campaigns\/?$/, () => ({ name: 'campaigns' })],
  [new RegExp(`^/campaigns/${SEG}/?$`), (m) => ({ name: 'campaign', campaign: m[1] })],
  [
    new RegExp(`^/campaigns/${SEG}/runs/${SEG}/?$`),
    (m, q) => ({ name: 'run', campaign: m[1], run: m[2], density: posInt(q.get('density')) }),
  ],
  [
    new RegExp(`^/campaigns/${SEG}/runs/${SEG}/trials/${SEG}/?$`),
    (m, q) => ({ name: 'trial', campaign: m[1], run: m[2], trial: m[3], microvm: posInt(q.get('microvm')) }),
  ],
  [/^\/builder\/?$/, () => ({ name: 'builder' })],
  [/^\/method\/?$/, () => ({ name: 'method', anchor: null })],
  [/^\/method\/([a-z0-9-]+)\/?$/, (m) => ({ name: 'method', anchor: m[1] })],
  [/^\/about\/?$/, () => ({ name: 'about' })],
];

function posInt(s: string | null): number | null {
  if (s === null || !/^[1-9]\d*$/.test(s)) return null;
  return Number(s);
}

export function parse(hash: string): Route {
  const raw = hash.replace(/^#/, '');
  const [path, query = ''] = raw.split('?');
  const q = new URLSearchParams(query);
  for (const [re, make] of PATTERNS) {
    const m = re.exec(path);
    if (m) return make(m, q);
  }
  return { name: 'not_found', path: raw };
}

export function href(r: Route): string {
  switch (r.name) {
    case 'home':
      return '#/';
    case 'campaigns':
      return '#/campaigns';
    case 'campaign':
      return `#/campaigns/${r.campaign}`;
    case 'run':
      return `#/campaigns/${r.campaign}/runs/${r.run}${r.density ? `?density=${r.density}` : ''}`;
    case 'trial':
      return `#/campaigns/${r.campaign}/runs/${r.run}/trials/${r.trial}${r.microvm ? `?microvm=${r.microvm}` : ''}`;
    case 'builder':
      return '#/builder';
    case 'method':
      return r.anchor ? `#/method/${r.anchor}` : '#/method';
    case 'about':
      return '#/about';
    case 'not_found':
      return `#${r.path}`;
  }
}

/** Which area of the site a route belongs to, for the navigation. */
export function area(r: Route): 'campaigns' | 'builder' | 'method' | 'about' | null {
  switch (r.name) {
    case 'home':
    case 'campaigns':
    case 'campaign':
    case 'run':
    case 'trial':
      return 'campaigns';
    case 'builder':
    case 'method':
    case 'about':
      return r.name;
    default:
      return null;
  }
}
