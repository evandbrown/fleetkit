// The current route, as reactive state.
import { parse, type Route } from './router';

export const nav: { route: Route } = $state({ route: parse(location.hash) });

window.addEventListener('hashchange', () => {
  nav.route = parse(location.hash);
});
