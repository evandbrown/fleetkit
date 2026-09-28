// Loads the dataset. Every URL is relative to the page (./data/...), so the site works under any path prefix.
import type { CampaignDoc, Index, RunDoc, TrialDoc } from './types';

const ID = /^[a-z0-9][a-z0-9-]*$/;

export class DataError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
  ) {
    super(status === 404 ? `Not in this dataset: ${path}` : `Couldn't load ${path} (${status || 'network error'})`);
  }
}

const cache = new Map<string, Promise<unknown>>();

function get<T>(path: string): Promise<T> {
  let p = cache.get(path) as Promise<T> | undefined;
  if (!p) {
    p = fetch(`./data/${path}`)
      .then(async (res) => {
        if (!res.ok) throw new DataError(path, res.status);
        return (await res.json()) as T;
      })
      .catch((e) => {
        cache.delete(path); // let a retry fetch again
        throw e instanceof DataError ? e : new DataError(path, 0);
      });
    cache.set(path, p);
  }
  return p;
}

function id(x: string): string {
  if (!ID.test(x)) throw new DataError(x, 404);
  return x;
}

/** Each document's path within the dataset (DATA.md, Files). */
export const campaignPath = (c: string) => `campaigns/${id(c)}/campaign.json`;
export const runPath = (c: string, r: string) => `campaigns/${id(c)}/runs/${id(r)}.json`;
export const trialPath = (c: string, r: string, t: string) => `campaigns/${id(c)}/runs/${id(r)}/${id(t)}.json`;

export const loadIndex = () => get<Index>('index.json');
export const loadCampaign = (c: string) => get<CampaignDoc>(campaignPath(c));
export const loadRun = (c: string, r: string) => get<RunDoc>(runPath(c, r));
export const loadTrial = (c: string, r: string, t: string) => get<TrialDoc>(trialPath(c, r, t));

export const imgUrl = (sha8: string, size: 't' | 'f' = 't') => `./data/img/${sha8}.${size}.webp`;
