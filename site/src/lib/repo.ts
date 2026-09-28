// Links to what is already public on GitHub (D73): a campaign's definition, the dataset file a page shows, and the
// harness code at the exact commit a run used. The dataset holds only repository paths and commits (it never ships
// a URL); these turn them into links. The raw evidence under results/ stays private, so nothing here points at it.
// A commit comes abbreviated to 10 hex characters (DATA.md, rule 9); GitHub resolves it, as the builder checked it
// names one commit alone.
import { campaignPath, runPath, trialPath } from './data';
import type { CampaignDoc, RunDoc } from './types';

export const REPO_URL = 'https://github.com/evandbrown/fleetkit';
/** Where the published dataset lives in the repository. */
export const DATA_DIR = 'site/public/data';

export interface SourceLink {
  label: string;
  href: string;
}

/** A file on the default branch. */
export const blobUrl = (path: string) => `${REPO_URL}/blob/main/${path}`;
/** The repository at one commit (the dataset's 10 characters). */
export const treeUrl = (commit: string) => `${REPO_URL}/tree/${commit}`;
/** A commit's label: its first 7 characters. */
export const shortCommit = (commit: string) => commit.slice(0, 7);

/** The harness commits, each once, in the order given; runs that recorded none are skipped. */
const commitLinks = (commits: (string | null)[]): SourceLink[] =>
  [...new Set(commits.filter((x): x is string => !!x))].map((x) => ({ label: `Harness @ ${shortCommit(x)}`, href: treeUrl(x) }));

function links(c: CampaignDoc, dataPath: string, commits: (string | null)[]): SourceLink[] {
  if (c.synthetic) return []; // test fixtures: nothing of theirs is on GitHub
  return [
    ...(c.definition_path ? [{ label: 'Definition', href: blobUrl(c.definition_path) }] : []),
    { label: 'Data', href: blobUrl(`${DATA_DIR}/${dataPath}`) },
    ...commitLinks(commits),
  ];
}

/** A campaign page: its definition, its campaign.json, and the harness its runs used (one link per commit). */
export const campaignSource = (c: CampaignDoc) => links(c, campaignPath(c.id), c.runs.map((r) => r.harness_commit));
/** A run page: the campaign's definition, the run's document, and the harness it used. */
export const runSource = (c: CampaignDoc, r: RunDoc) => links(c, runPath(c.id, r.id), [r.harness_commit]);
/** A trial page: as its run's, with the trial's own document as the data. */
export const trialSource = (c: CampaignDoc, r: RunDoc, trial: string) =>
  links(c, trialPath(c.id, r.id, trial), [r.harness_commit]);
