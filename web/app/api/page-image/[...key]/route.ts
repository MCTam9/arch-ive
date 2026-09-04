// Authenticated proxy for page renders.
//
// The Blob store is private and its URLs are never rendered into HTML. A page
// image is a picture of a client-confidential document, so it is reachable
// only here, only with a session, and only for a page that belongs to a
// document the caller's account can already see -- the last part matters,
// because a signed-in reader guessing a blob pathname must not be able to
// pull a page out of a document RLS would otherwise hide from them.
import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { withAccount } from "@/lib/db";
import { get } from "@vercel/blob";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ key: string[] }> },
) {
  const session = await auth();
  if (!session?.accountId) {
    return new NextResponse("unauthorized", { status: 401 });
  }

  const { key } = await params;
  const pathname = `pages/${key.join("/")}`;

  // RLS decides, not the URL. If this account cannot see the page row, the
  // image does not exist as far as they are concerned.
  const allowed = await withAccount(session.accountId, async (client) => {
    const { rows } = await client.query(
      "SELECT 1 FROM source_page WHERE page_image_key = $1 LIMIT 1",
      [pathname],
    );
    return rows.length > 0;
  });
  if (!allowed) {
    return new NextResponse("not found", { status: 404 });
  }

  // get(), not head() + fetch(downloadUrl): on a private store that URL is
  // not fetchable without credentials, and the plain fetch 502s. get() reads
  // through the SDK's authenticated path and hands back a stream.
  const result = await get(pathname, { access: "private", abortSignal: AbortSignal.timeout(15_000) }).catch(
    () => null,
  );
  if (!result?.stream) {
    return new NextResponse("not found", { status: 404 });
  }

  return new NextResponse(result.stream as unknown as ReadableStream, {
    headers: {
      "Content-Type": result.blob?.contentType ?? "image/webp",
      // private: this is per-account authorised content, never a shared cache
      "Cache-Control": "private, max-age=3600",
    },
  });
}
