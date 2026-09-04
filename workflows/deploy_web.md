# Workflow: deploy the web app

**Objective.** Put the browse, matrix and review views in front of approved
accounts only, on Vercel's free tier, without any corpus material becoming
publicly fetchable.

## Tools

| | |
|---|---|
| `tools/upload_page_images.py` | page renders → the private R2 bucket (`--verify` checks the database's keys against it) |
| `web/scripts/build_fonts.py` | display face OTF → WOFF2 for the deployment |
| `web/.vercelignore` | what must never reach a public URL |
| `tools/manage_allowlist.py` | who may sign in (see `workflows/provision_database.md`) |

## The rule that shapes all of it

**Anything under `web/public/` is served unauthenticated.** Not "hard to
find" — public. So nothing corpus-derived goes there, and neither does
anything licensed for one machine. Two things have already had to be pulled
back out of it.

## Page images

The renders live in `.tmp/pages/`, which CLAUDE.md defines as disposable, so
the deployed app cannot show a page at all until they are uploaded.

```sh
python -m tools.upload_page_images            # uploads only what is missing
python -m tools.upload_page_images --verify   # every DB key resolves to an object
```

`source_page.page_image_key` holds the object key verbatim
(`pages/<document uuid>/<page>.webp`) on **every** database, local and Neon —
a key that resolves on one and not the other is a broken image that only shows
up in production.

**Why R2 rather than Vercel Blob.** Blob counts `put()`, `copy()` and `list()`
as Advanced Operations and Hobby includes **2,000 a month**. Uploading this
corpus once is 1,512 puts plus the listing — 76% of the month in a single run,
and passing the cap cuts off the store for the remainder of the 30 days, taking
every page render off the site with it. That was not a theoretical limit: the
first upload triggered the 75% warning email. R2 is roughly 1M writes and 10M
reads a month with zero egress, which also makes re-rendering the corpus after
a pipeline change free rather than a budgeting decision. Reads through the
proxy were never the problem — `get()` is not an Advanced Operation — but the
headroom on writes is what stops the next re-render being a decision.

The bucket is private, and keys are deliberately opaque: unlike the originals,
page renders are **not** client-side encrypted, so the object names must not
describe what they hold.

Serving is via `web/app/api/page-image/[...key]/route.ts` (client in
`web/lib/pages-bucket.ts`), never a bucket URL in the HTML. The route checks
the session **and** that the page belongs to a document the caller's own
account can see under RLS. The second check is the point: a signed-in reader
guessing a key must not reach a page from a document RLS would otherwise hide.

**Follow-up worth doing:** the credentials in the Vercel environment are the
account-scoped R2 token. Mint one in the Cloudflare dashboard scoped to
*Object Read only* on `arch-ive-pages` and swap it in — the web app never
writes to this bucket, and it has no business being able to reach the
originals bucket at all, even as ciphertext.

## Fonts

```sh
python3 web/scripts/build_fonts.py
```

Converts the display OTF to WOFF2; `web/.vercelignore` keeps the `.otf` out of
the upload entirely. The OTF was served from `/public` for a while — an
installable commercial desktop binary, downloadable with no session, which is a
different thing from serving a webfont under most foundry licences. WOFF2 is
also about a third of the bytes.

Both formats stay gitignored. A clone without them falls through to Jacquard 12
from Google Fonts, so the page still renders.

## Deploy

```sh
cd web && npx vercel --prod --yes --scope <team>
```

The CLI always prints a *"Promote to production"* line in its closing `next`
block — it is a generic hint, not a sign the deploy stayed in preview. Read
`readyState` and `target` in the JSON instead, then confirm the alias actually
moved:

```sh
vercel inspect arch-ive.vercel.app --scope <team>   # url must be the new deployment
```

Environment variables live in the Vercel project, not in the repo:
`DATABASE_URL` (**pooled**), `AUTH_DB_URL` (the `arch_auth` role),
`AUTH_SECRET`, `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET`, and `R2_ACCOUNT_ID` /
`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET_PAGES`. Add them with
`vercel env add <NAME> production` reading the value from stdin, so a secret
never lands in shell history.

Note the deviation from CLAUDE.md, recorded rather than hidden: Next.js reads
its own `web/.env` and `web/.env.local`, so secrets exist in two places on this
machine. Both are gitignored and `scripts/check_wat.py` fails on any env file
outside that sanctioned set.

## Verify — the gates, not the pixels

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/api/page-image/<key>   # 401
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/fonts/*.otf            # 404
```

- page image, no session → **401**; a key the account cannot see → **404**;
  legitimate request → **200** and real image bytes.
- sign in with an address absent from `allowed_account` → rejected.
- revoke a row, then use the live session → access lost at the next check.
- the review queue's approve/reject re-checks the role **server-side**, not
  just by disabling the button; a reader posting the form directly is refused.

Do not rely on Vercel Deployment Protection: on Hobby it covers previews, and
production protection is paid. The gate is in the app.
