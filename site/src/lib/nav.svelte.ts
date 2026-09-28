// The current route, as reactive state. An older address opens its page and is rewritten in place to the new one,
// without adding a history entry (router.ts, resolve).
import { resolve, type Route } from './router';

function current(): Route {
  const { route, redirect } = resolve(location.hash);
  if (redirect !== null) {
    try {
      history.replaceState(history.state, '', redirect);
    } catch {
      // a sandboxed frame may refuse; the page still opens at the older address
    }
  }
  return route;
}

export const nav: { route: Route } = $state({ route: current() });

window.addEventListener('hashchange', () => {
  nav.route = current();
});
