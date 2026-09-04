// Push the rendered page images to Vercel Blob.
//
// They are produced into .tmp/pages, which CLAUDE.md defines as disposable --
// so until they live in Blob the deployed app cannot show a page render at
// all, and the review queue (extracted record beside the actual scan) cannot
// exist. The store is PRIVATE: these are page renders of client-confidential
// documents and must only ever be reachable through an authenticated route.
//
// Idempotent: it lists what is already there and skips it, so a re-run after
// an interruption uploads only the remainder.
import { readdir, readFile, stat } from "node:fs/promises";
import { join, relative } from "node:path";
import { put, list } from "@vercel/blob";

const ROOT = "../.tmp/pages"; // repo-root .tmp, from web/
const CONCURRENCY = 8;

async function* walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (entry.name.endsWith(".webp")) yield full;
  }
}

async function existingPathnames() {
  const seen = new Set();
  let cursor;
  do {
    const page = await list({ cursor, limit: 1000, prefix: "pages/" });
    for (const b of page.blobs) seen.add(b.pathname);
    cursor = page.cursor;
  } while (cursor);
  return seen;
}

const files = [];
for await (const f of walk(ROOT)) files.push(f);
files.sort();

const already = await existingPathnames();
const todo = files.filter((f) => !already.has(`pages/${relative(ROOT, f)}`));
console.log(`${files.length} rendered pages, ${already.size} already uploaded, ${todo.length} to send`);

let done = 0;
let bytes = 0;
async function worker(queue) {
  while (queue.length) {
    const file = queue.pop();
    const pathname = `pages/${relative(ROOT, file)}`;
    const body = await readFile(file);
    await put(pathname, body, {
      // Private, matching the store. These are page renders of
       // client-confidential documents; they are reachable only through an
       // authenticated route, never by URL.
      access: "private",
      addRandomSuffix: false,
      allowOverwrite: true, // a re-run after an interruption must not fail
      contentType: "image/webp",
      cacheControlMaxAge: 31536000,
    });
    bytes += (await stat(file)).size;
    if (++done % 100 === 0) console.log(`  ${done}/${todo.length}`);
  }
}

const queue = todo.slice();
await Promise.all(Array.from({ length: CONCURRENCY }, () => worker(queue)));
console.log(`uploaded ${done} page image(s), ${(bytes / 1e6).toFixed(1)} MB`);
