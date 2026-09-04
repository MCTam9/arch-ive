// Authenticated proxy for page renders.
//
// The bucket is private and its URLs are never rendered into HTML. A page
// image is a picture of a client-confidential document, so it is reachable
// only here, only with a session, and only for a page that belongs to a
// document the caller's account can already see -- the last part matters,
// because a signed-in reader guessing a key must not be able to pull a page
// out of a document RLS would otherwise hide from them.
import { NextResponse } from "next/server";
import { GetObjectCommand } from "@aws-sdk/client-s3";
import { auth } from "@/auth";
import { withAccount } from "@/lib/db";
import { pagesBucket } from "@/lib/pages-bucket";

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
  // Reject traversal before it reaches the bucket. The DB check below would
  // already fail closed, but a key that can climb out of the prefix has no
  // business being constructed at all.
  if (key.some((seg) => seg === "." || seg === ".." || seg.includes("\\"))) {
    return new NextResponse("not found", { status: 404 });
  }
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

  const { s3, bucket } = pagesBucket();
  const object = await s3
    .send(new GetObjectCommand({ Bucket: bucket, Key: pathname }))
    .catch(() => null);
  if (!object?.Body) {
    return new NextResponse("not found", { status: 404 });
  }

  // transformToWebStream(): the SDK hands back a Node Readable under the
  // nodejs runtime, and NextResponse wants a web stream. Streaming rather
  // than buffering matters because the function never holds the image.
  return new NextResponse(object.Body.transformToWebStream(), {
    headers: {
      "Content-Type": object.ContentType ?? "image/webp",
      // private: this is per-account authorised content, never a shared cache
      "Cache-Control": "private, max-age=3600",
    },
  });
}
