// The page-render store: a private R2 bucket, reached with S3 credentials.
//
// This replaced Vercel Blob, which counts put/copy/list as "Advanced
// Operations" and includes 2,000 a month on Hobby -- one upload of this corpus
// is 1,512 of them, and passing the cap cuts off the store entirely, taking
// every page render off the site. R2 is ~1M writes and 10M reads a month with
// zero egress.
//
// Keys are byte-identical to the Blob pathnames they replaced
// (`pages/<document uuid>/<page>.webp`), so source_page.page_image_key did not
// change. They are opaque on purpose: these objects are not client-side
// encrypted the way the originals are, so a bucket listing must not describe
// what it holds.
import { S3Client } from "@aws-sdk/client-s3";

let client: S3Client | null = null;

export function pagesBucket(): { s3: S3Client; bucket: string } {
  const account = process.env.R2_ACCOUNT_ID;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;
  const bucket = process.env.R2_BUCKET_PAGES;
  if (!account || !accessKeyId || !secretAccessKey || !bucket) {
    throw new Error(
      "page store is unconfigured: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_PAGES",
    );
  }
  client ??= new S3Client({
    region: "auto",
    endpoint: `https://${account}.r2.cloudflarestorage.com`,
    credentials: { accessKeyId, secretAccessKey },
  });
  return { s3: client, bucket };
}
