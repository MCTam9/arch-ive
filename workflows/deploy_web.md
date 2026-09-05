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
| `tools/set_document_titles.py` | human labels for the 14 documents |
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

### Scoping the token the web app runs on

**Done — production has run on a read-only token since 2026-09-05.** The
procedure below is kept because it is also the rotation procedure, and
because `tools/check_r2_token.py` is worth running against whatever is
configured whenever the credentials change hands.

The web app only reads page renders, so its credentials should do only that.
They started as the account-scoped R2 token, which can also write this bucket
and list the originals bucket — those objects are client-side encrypted, but a
web function still has no business reaching them.

A token swap fails silently in the direction that matters: too little scope
takes every page render off the site, too much looks perfectly healthy. So it
is checked in both directions before the value goes near production.

1. Cloudflare dashboard → **R2 → API → Manage API tokens → Create API token**.
   Permission **Object Read only**, applied to **`arch-ive-pages` only**, not
   to all buckets. Cloudflare shows the access key id and secret exactly once.
2. Put the pair in `.tmp/r2-read.env` — gitignored and disposable by
   CLAUDE.md's definition — as `R2_ACCESS_KEY_ID=` and `R2_SECRET_ACCESS_KEY=`.
3. Prove it before shipping it:
   ```sh
   python3 -m tools.check_r2_token --env-file .tmp/r2-read.env
   ```
   Four checks: it can read a real key taken from `source_page`, and it cannot
   write, delete, or reach the originals bucket. The write and delete checks
   act on a `_token-check/` probe key, never on a live render — a delete check
   aimed at a real key would destroy a page the moment it failed the wrong way.
   Run it with no `--env-file` to audit whatever is configured right now.
4. Swap it in, reading each value from stdin so it misses shell history:
   ```sh
   cd web
   npx vercel env rm R2_ACCESS_KEY_ID production --yes
   npx vercel env rm R2_SECRET_ACCESS_KEY production --yes
   npx vercel env add R2_ACCESS_KEY_ID production      # paste, then Ctrl-D
   npx vercel env add R2_SECRET_ACCESS_KEY production
   npx vercel --prod --yes
   ```
   Environment variables are read at deploy time, so the redeploy is the step
   that applies them — changing them alone leaves the old token running.
5. Confirm against the live site that a signed-in page render still returns
   image bytes. A 500 from `/api/page-image` after this means the token cannot
   read; roll back by re-adding the previous pair.
6. Delete `.tmp/r2-read.env`. Leave the account-scoped token in `.env` alone —
   `tools/upload_page_images.py` and `rclone` read that file, not Vercel, and
   they still have to write. Revoking it in Cloudflare would break both for no
   gain: the exposure this closes is what the *deployed function* can reach,
   and that is now read-only on one bucket.

## Document titles

```sh
python -m tools.set_document_titles            # dry run
python -m tools.set_document_titles --apply    # on every database
```

`source_document.title` was NULL for all 14 documents, so the whole interface
labelled them `crib-water` and `framework-vol-e1`. Slugs remain the only
identifier permitted in code, commits and issues (CONTRACT.md) — this is a
display label only, and it lives in `private/documents.yaml` so the public repo
never carries it. A slug with no entry falls back to one derived from the slug,
so a fresh clone still reads sensibly.

**These strings render in the UI.** No client, consultant or project name goes
in them. `scripts/scan_forbidden.py` scans the repo, not the database, so this
one is not enforced by a gate.

## Region

`web/vercel.json` pins functions to `lhr1`, beside Neon in `eu-west-2`. They
ran in the default `iad1` (Washington DC), so every database round trip crossed
the Atlantic — TTFB roughly halved on the routes that make no queries at all,
and the query-heavy pages gain far more. Hobby allows a single region; check it
took effect by reading the **second** field of `x-vercel-id` (`lhr1::lhr1::…`
is right, `lhr1::iad1::…` means the edge is London and the function is not).

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
