# gsc-cli

Headless Google Search Console for one property. Pure Python standard library — no
venv, no `pip install`, no SDK. On a laptop there is no key file at all; on a
headless box, one scoped key and nothing else.

Built so a coding agent can drive Search Console itself: pull query data, check
whether pages are indexed, submit sitemaps, without anyone clicking through the UI.

```sh
export GSC_SITE='https://www.example.com/'
./gsc.py q --dims query --days 28 --limit 30
```

See **[SETUP.md](SETUP.md)** for the service account, the property permission, and
wiring it into Claude Code. Fifteen minutes, once.

## Commands

```sh
gsc sites                              # properties this account can read
gsc q --dims query --limit 30
gsc q --dims page,query --days 7 --filter page:contains:/blog
gsc q --dims country --days 90
gsc compare --dim query --min-clicks 100           # gainers
gsc compare --dim page --worst                     # losers
gsc sitemaps                           # status + submitted counts
gsc inspect <url> [<url>...]           # live index status per URL
gsc audit                              # inspect every sitemap URL, report problems only
gsc snapshot --days 7                  # append history to data/
```

Writes — they change what Google sees, so run them deliberately:

```sh
gsc sitemap-submit https://www.example.com/sitemap.xml
gsc sitemap-delete https://www.example.com/old-sitemap.xml
```

`--json` works on any command, before or after the subcommand.
`--dims` accepts `query,page,country,device,date,searchAppearance`.
`--filter` is `dimension:operator:expression`, repeatable; operators are
`equals`, `notEquals`, `contains`, `notContains`, `includingRegex`, `excludingRegex`.

Handy alias:

```sh
alias gsc="$PWD/gsc.py"
```

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `GSC_SITE` | yes | The property, spelled exactly as Search Console shows it. `https://www.example.com/` for URL-prefix (trailing slash), `sc-domain:example.com` for Domain. |
| `GSC_SA` | no | Service account email to impersonate via `gcloud`. Omit to use whatever `gcloud` is logged in as. |
| `GSC_KEY_FILE` | no | Path to a service-account key JSON. Takes precedence over `gcloud`, and removes the need for it entirely — see below. |

`--site` overrides `GSC_SITE` per invocation.

## Two ways to authenticate

**On a laptop — keyless.** `gcloud` mints a short-lived token, optionally
impersonating a service account. Nothing to rotate, nothing to leak.

**On a headless box — a key file.** Keyless impersonation needs a human login to
impersonate *from*, so it cannot work on a VPS or in CI. Set `GSC_KEY_FILE` and
`gsc.py` does the OAuth2 JWT-bearer exchange itself: it builds the JWT, signs it
RS256 by handing the key to `openssl` through a pipe (never a temp file), and
exchanges it for a token. **gcloud is not required on that machine** — only
`python3` and `openssl`.

Do not run `gcloud auth login` on a server to get around this. That leaves a
refresh token for your whole Google account on the box; a scoped service-account
key is far less to lose. [SETUP.md](SETUP.md#headless-machines-vps-ci-containers)
covers making that account powerless enough to be safe.

## Things that will mislead you

These are all Google's behaviour, not this tool's, and every one distorts analysis
silently rather than erroring.

**Two API hosts, one 403.** Search analytics, sitemaps and sites run on the legacy
`www.googleapis.com/webmasters/v3` host, which needs **no API enabled**. Only URL
Inspection uses `searchconsole.googleapis.com`. So a 403 on `inspect` while `q`
works is an unenabled API, not a permissions problem.

**Data lags 2–3 days.** Every window here already ends three days back. Reading a
partial final day as a traffic drop is the classic false alarm.

**`d_position` is inverted.** In `compare` output it is previous minus current, so
**positive means improved** — the page moved up. Every other delta is current minus
previous, where positive means grew. Sign errors here reverse your conclusions.

**~5,000 distinct `query` rows per day is a hard ceiling.** (`page`+`query` gets you
to roughly 11,300.) Google returns nothing past it whatever `startRow` asks for. So
*any* per-query breakdown is Google's own truncation of a longer tail, and per-query
sums will not reconcile with site totals. Site-level totals are complete; anything
grouped by query is not.

**Rows come back ordered by clicks descending.** Requesting a multi-day range and
taking the first N therefore does *not* give "the top queries for that period" — it
gives every clicked query plus an arbitrary slice of the zero-click tail. The
queries with real impressions and no clicks are usually the ranking opportunities,
and they are exactly what gets dropped. Pull day by day when the tail matters.

**Brand queries wreck any CTR-by-position curve.** Brand terms convert far better
at the same position than everything else, so a curve built from all queries reads
much too optimistic. Exclude brand with
`--filter query:notContains:<brand>`, and when estimating upside use the target
position's own non-brand CTR rather than the best CTR in the band above it.

**`canonical` reports `self (none declared)`** when a page ships no canonical tag.
Untidy but harmless — Google self-canonicalises. Only `MISMATCH ->` needs action.

**`snapshot` appends.** Re-running it for the same dates duplicates rows;
de-duplicate on `(date, query, page)` when reading, or clear `data/` first.

**16 months of history, maximum.** Older start dates return HTTP 400.

## Quotas

- Search Analytics: 25,000 rows per request (paginated automatically), ~1,200
  requests/day.
- URL Inspection: **2,000 URLs/day**, 600/minute. Each call carries ~10s of API
  latency, so serial sweeps are painfully slow — `audit` runs 8 workers (~100 URLs
  in about 100 seconds).

## What the UI can do that the API cannot

No endpoint exists for these; they stay manual:

- **Manual actions** and **Security issues** — read-only in the UI.
- **Core Web Vitals / Page Experience** — use the CrUX API instead.
- **Crawl stats** report.
- **Links** report (internal and external backlinks) — no endpoint at all.
- **Removals** and **URL parameters** tools.
- **Adding a user to a property** — the one manual step in SETUP.md.
- **Request indexing.** The Indexing API is officially limited to `JobPosting` and
  `BroadcastEvent` schema; using it for arbitrary pages violates Google's terms.
  Sitemap freshness and internal linking are the legitimate levers.
- **Bulk export to BigQuery** is configured once in the UI, then becomes the best
  source for unsampled analysis.

## Licence

MIT.
