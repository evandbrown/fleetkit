// Colours that carry data, by job. Green and red mean passed and failed and nothing else; specs have their own hues
// (tokens.css --spec-*), the steps theirs (--step-*), and host CPU by process is greys with the hypervisor picked out.
import type { HostConsumer } from './types';

/** A spec's colour, by its place in the campaign (or the compare page's list). */
export const specColor = (i: number) => `var(--spec-${(i % 4) + 1})`;

export const HOST_CONSUMER_COLOR: Record<HostConsumer, string> = {
  microvm_vcpus: 'var(--proc-microvm)',
  hypervisor: 'var(--proc-hypervisor)',
  hostd: 'var(--proc-hostd)',
  driver: 'var(--proc-driver)',
  unattributed: 'var(--proc-unattributed)',
};
