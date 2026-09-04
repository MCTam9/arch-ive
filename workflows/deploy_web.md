# Workflow: deploy the web app

**Objective.** Put the browse, matrix and review views in front of approved
accounts only, on Vercel's free tier, without any corpus material becoming
publicly fetchable.

## Tools

| | |
|---|---|
| `web/scripts/upload_page_images.mjs` | page renders → the private Blob store |
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
the deployed app could not show a page at all until they were uploaded.

```sh
vercel blob create-store arch-ive-pages --access private --yes --scope <team>
cd web && node scripts/upload_page_images.mjs
```

Then rewrite `source_page.page_image_key` from `.tmp/pages/...` to `pages/...`
on **every** database, local and Neon — a key that resolves on one and not the
other is a broken image that only appears in production.

Two SDK constraints, both found the hard way:

- a **private store rejects per-blob `access: "public"`** — the uploader must
  declare private;
- **`head()` + `fetch(downloadUrl)` 502s on a private blob**, because that URL
  needs credentials. Use `get()` with `access: "private"`, which reads through
  the authenticated path.

Serving is via `web/app/api/page-image/[...key]/route.ts`, never a Blob URL in
the HTML. The route checks the session **and** that the page belongs to a
document the caller's own account can see under RLS. The second check is the
point: a signed-in reader guessing a pathname must not reach a page from a
document RLS would otherwise hide.

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

The CLI sometimes builds and then prints a *"Promote to production"* hint
instead of promoting; run it again rather than assuming the first call landed.

Environment variables live in the Vercel project, not in the repo:
`DATABASE_URL` (**pooled**), `AUTH_DB_URL` (the `arch_auth` role),
`AUTH_SECRET`, `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET`, `BLOB_READ_WRITE_TOKEN`.

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
