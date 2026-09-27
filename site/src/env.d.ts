/// <reference types="svelte" />
/// <reference types="vite/client" />

declare module 'virtual:method' {
  /** docs/method.md, rendered at build time. */
  export const html: string;
  export const headings: { depth: number; id: string; text: string }[];
}
