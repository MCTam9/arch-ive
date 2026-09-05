// Auth gate for protected routes. Deliberately NOT done in proxy.ts: Next 16
// renamed middleware to Proxy and its own docs say it "should not be used as
// a full session management or authorization solution" — and in this app it
// would also be the wrong runtime, since the allowlist check in auth.ts's
// callbacks goes through `pg`, which needs the Node.js runtime that Proxy
// doesn't guarantee. Instead every protected route sits under
// app/(protected)/layout.tsx, which calls requireSession() once per request.
import { cache } from "react";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { href, safePath } from "@/lib/links";

// Wrapped in React's request-scoped cache() because the layout calls this and
// then so does every page under it — two JWT decodes per request for one
// answer that cannot change mid-render. cache() dedupes within a single
// request and shares nothing across requests, so a session is never reused by
// a different visitor.
export const requireSession = cache(async () => {
  const session = await auth();
  if (!session?.accountId) {
    // Where they were going, so signing in finishes the journey instead of
    // ending it on the home page. This used to be a bare redirect("/login"),
    // which was harmless only while `/` and browse were the same page: a
    // shared /browse?topic=…&q=… link handed to someone signed out came back
    // as home, with the filters silently gone. proxy.ts supplies the path;
    // safePath refuses anything that is not a path on this site, so `next`
    // cannot be turned into an open redirect.
    const next = safePath((await headers()).get("x-pathname"));
    redirect(href("/login", { next: next ?? undefined }));
  }
  return session as NonNullable<typeof session> & { accountId: string };
});
