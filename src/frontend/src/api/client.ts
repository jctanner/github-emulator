import createClient, {type Middleware} from "openapi-fetch";

import type {paths} from "./schema";

let csrfToken: string | null = null;

export function setCsrfToken(value: string | null): void {
  csrfToken = value;
}

const csrfMiddleware: Middleware = {
  onRequest({request}) {
    if (
      csrfToken &&
      !["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase())
    ) {
      request.headers.set("X-CSRF-Token", csrfToken);
    }
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: globalThis.location.origin,
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
});

api.use(csrfMiddleware);
