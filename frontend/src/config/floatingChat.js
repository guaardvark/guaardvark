/**
 * Routes where the floating chat stays out of the way.
 *
 * A page that is itself a chat surface does not need a second one floating
 * over its send button. The engine lists its own Chat page; a vertical
 * distribution overrides this file to add its chat-shaped pages (a command
 * bar, a knowledge-base page) rather than forking the provider.
 *
 * Entries are exact paths, or a prefix ending in "*" that covers every path
 * beneath it.
 */
export const FLOATING_CHAT_HIDDEN_ROUTES = ["/chat"];

export const isFloatingChatHiddenRoute = (pathname, routes = FLOATING_CHAT_HIDDEN_ROUTES) =>
  routes.some((route) =>
    route.endsWith("*") ? pathname.startsWith(route.slice(0, -1)) : pathname === route
  );
