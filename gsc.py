#!/usr/bin/env python3
"""Google Search Console CLI — headless access to one property, standard library only.

Configure with environment variables (see SETUP.md):

    GSC_SITE      the property, exactly as Search Console shows it.
                  URL-prefix:  https://www.example.com/   (trailing slash)
                  Domain:      sc-domain:example.com
    GSC_SA        optional. A service account email to impersonate. Omit to use
                  whatever `gcloud auth print-access-token` is already logged in as.
    GSC_KEY_FILE  optional. Path to a service-account key JSON, for headless
                  machines where there is no gcloud login to impersonate from.
                  Takes precedence over gcloud; needs only python3 and openssl.

Docs: https://developers.google.com/webmasters/v3/
"""
import argparse, base64, json, os, subprocess, sys, time
import urllib.request, urllib.error, urllib.parse
from concurrent import futures
from datetime import date, timedelta
from pathlib import Path

SA = os.environ.get("GSC_SA") or None
SCOPE = "https://www.googleapis.com/auth/webmasters"
SITE = os.environ.get("GSC_SITE") or None
KEY_FILE = os.environ.get("GSC_KEY_FILE") or None
BASE = "https://www.googleapis.com/webmasters/v3"
DATA = Path(__file__).parent / "data"

# Search Console data lags ~2-3 days; default windows end there to avoid
# reading a partial final day as a drop.
LAG_DAYS = 3


_TOKEN = None


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _sign_rs256(signing_input, private_key_pem):
    """RS256 via the openssl binary, so this file stays standard-library only.

    The key is handed to openssl through a pipe and referenced as /dev/fd/N rather
    than written to a temp file: a service-account key should not touch the disk a
    second time, least of all in /tmp where it would outlive a crash. PEM keys are
    ~1.7KB, well inside the 64KB pipe buffer, so writing before exec cannot block.
    """
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, private_key_pem.encode())
        os.close(w_fd)
        w_fd = None
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{r_fd}"],
            input=signing_input, capture_output=True, pass_fds=(r_fd,))
    finally:
        if w_fd is not None:
            os.close(w_fd)
        os.close(r_fd)

    if proc.returncode != 0:
        sys.exit(f"openssl could not sign the JWT: {proc.stderr.decode()[:400]}")
    return proc.stdout


def _token_from_key_file(path):
    """The JWT-bearer flow, for headless machines.

    gcloud's keyless impersonation needs a human login to impersonate *from*, which
    a server does not have. A service-account key is the alternative, and exchanging
    it for an access token is a signed JWT and one POST -- no SDK, no dependencies.
    """
    try:
        key = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"Could not read GSC_KEY_FILE at {path}: {e}")

    for field in ("client_email", "private_key"):
        if not key.get(field):
            sys.exit(f"{path} is missing `{field}` -- is it a service-account key JSON?")

    aud = key.get("token_uri", "https://oauth2.googleapis.com/token")
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": key["client_email"],
        "scope": SCOPE,
        "aud": aud,
        "iat": now,
        # Google caps assertion lifetime at an hour; the token it returns is
        # independent of this and lives for its own hour.
        "exp": now + 3600,
    }
    signing_input = _b64(json.dumps(header).encode()) + b"." + _b64(json.dumps(claims).encode())
    assertion = signing_input + b"." + _b64(_sign_rs256(signing_input, key["private_key"]))

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion.decode(),
    }).encode()
    req = urllib.request.Request(
        aud, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        return json.loads(urllib.request.urlopen(req).read())["access_token"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        hint = ""
        if "invalid_grant" in detail:
            hint = ("\n\ninvalid_grant usually means the key was deleted or the "
                    "machine clock is skewed -- the JWT is time-signed.")
        sys.exit(f"Token exchange failed: HTTP {e.code}: {detail}{hint}")


def _token_from_gcloud():
    cmd = ["gcloud", "auth", "print-access-token", f"--scopes={SCOPE}"]
    if SA:
        cmd.append(f"--account={SA}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(
            f"Could not mint a token via gcloud.\n{r.stderr.strip()}\n\n"
            "On a laptop: check gcloud is installed and logged in, and that "
            f"{'the service account ' + SA if SA else 'the active account'} "
            "can be impersonated.\n"
            "On a headless machine there is no login to impersonate from -- set "
            "GSC_KEY_FILE to a service-account key instead. See SETUP.md.")
    return r.stdout.strip()


def token():
    """Cached for the process. Either path costs ~0.3-1s, which is fine once but
    crippling on a sitemap-wide sweep that would otherwise re-mint per request."""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _token_from_key_file(KEY_FILE) if KEY_FILE else _token_from_gcloud()
    return _TOKEN


def call(path, body=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:800]}")


def write_call(method, path):
    req = urllib.request.Request(f"{BASE}{path}",
                                 headers={"Authorization": f"Bearer {token()}"}, method=method)
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:400]}")


def site_path(site):
    return urllib.parse.quote(site, safe="")


def window(args):
    end = args.end or str(date.today() - timedelta(days=LAG_DAYS))
    start = args.start or str(date.fromisoformat(end) - timedelta(days=args.days - 1))
    return start, end


def fetch_all(site, body, limit=1_000_000):
    """Search Analytics paginated past the 25k-rows-per-request cap."""
    rows, offset = [], 0
    while len(rows) < limit:
        page = dict(body, rowLimit=min(25000, limit - len(rows)), startRow=offset)
        batch = call(f"/sites/{site_path(site)}/searchAnalytics/query", page).get("rows", [])
        rows.extend(batch)
        if len(batch) < page["rowLimit"]:
            break
        offset += len(batch)
    return rows


def parse_filters(specs):
    out = []
    for f in specs or []:
        try:
            dim, op, expr = f.split(":", 2)
        except ValueError:
            sys.exit(f"--filter must be dimension:operator:expression, got {f!r}\n"
                     "e.g. page:contains:/blog")
        out.append({"dimension": dim, "operator": op, "expression": expr})
    return out


def query(args):
    """Search Analytics. Paginates so --limit can exceed the 25k per-request cap."""
    start, end = window(args)
    dims = [d.strip() for d in args.dims.split(",") if d.strip()]
    filters = parse_filters(args.filter)

    body = {"startDate": start, "endDate": end, "dimensions": dims,
            "type": args.type, "dataState": args.data_state}
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    rows = fetch_all(args.site, body, args.limit)

    out = [dict(zip(dims, r["keys"]),
                clicks=round(r["clicks"]), impressions=round(r["impressions"]),
                ctr=round(r["ctr"] * 100, 2), position=round(r["position"], 1))
           for r in rows]
    emit(out, dims + ["clicks", "impressions", "ctr", "position"], args, meta=f"{start}..{end}")


def compare(args):
    """Same query over two adjacent windows, joined, sorted by click delta."""
    end = args.end or str(date.today() - timedelta(days=LAG_DAYS))
    n = args.days
    cur_start = str(date.fromisoformat(end) - timedelta(days=n - 1))
    prev_end = str(date.fromisoformat(cur_start) - timedelta(days=1))
    prev_start = str(date.fromisoformat(prev_end) - timedelta(days=n - 1))
    dim = args.dim

    filters = parse_filters(args.filter)

    def pull(s, e):
        body = {"startDate": s, "endDate": e, "dimensions": [dim],
                "rowLimit": 25000, "type": args.type, "dataState": args.data_state}
        if filters:
            body["dimensionFilterGroups"] = [{"filters": filters}]
        return {r["keys"][0]: r for r in
                call(f"/sites/{site_path(args.site)}/searchAnalytics/query", body).get("rows", [])}

    cur, prev = pull(cur_start, end), pull(prev_start, prev_end)
    out = []
    for k in set(cur) | set(prev):
        c, p = cur.get(k), prev.get(k)
        cc, pc = (c["clicks"] if c else 0), (p["clicks"] if p else 0)
        if max(cc, pc) < args.min_clicks:
            continue
        out.append({
            dim: k, "clicks": round(cc), "prev_clicks": round(pc), "d_clicks": round(cc - pc),
            "impressions": round(c["impressions"] if c else 0),
            "d_impr": round((c["impressions"] if c else 0) - (p["impressions"] if p else 0)),
            "position": round(c["position"], 1) if c else None,
            # Position is "lower is better", so improvement is prev - cur.
            "d_position": round(p["position"] - c["position"], 1) if c and p else None,
        })
    out.sort(key=lambda r: r["d_clicks"], reverse=args.worst is False)
    emit(out[:args.limit], [dim, "clicks", "prev_clicks", "d_clicks", "impressions", "d_impr",
                            "position", "d_position"], args,
         meta=f"{cur_start}..{end} vs {prev_start}..{prev_end}")


def sitemap_submit(args):
    """PUT a sitemap. Google queues it; status shows up in `sitemaps` within a day."""
    for sm in args.urls:
        write_call("PUT", f"/sites/{site_path(args.site)}/sitemaps/{urllib.parse.quote(sm, safe='')}")
        print(f"submitted {sm}")


def sitemap_delete(args):
    """Unsubmit a sitemap. Does NOT deindex its URLs -- it only stops Google tracking it."""
    for sm in args.urls:
        write_call("DELETE", f"/sites/{site_path(args.site)}/sitemaps/{urllib.parse.quote(sm, safe='')}")
        print(f"deleted {sm}")


def audit(args):
    """Inspect every sitemap URL and report only what needs attention."""
    args.urls, args.sitemap = [], True
    rows = inspect(args, _return=True)
    problems = [r for r in rows
                if r.get("verdict") != "PASS"
                or "MISMATCH" in str(r.get("canonical"))
                or r.get("fetch") not in ("SUCCESSFUL", "")
                or r.get("robots") not in ("ALLOWED", "")]
    print(f"\n{len(rows)} URLs inspected, {len(problems)} needing attention\n", file=sys.stderr)
    counts = {}
    for r in rows:
        counts[r.get("coverage", "?")] = counts.get(r.get("coverage", "?"), 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>4}  {k}", file=sys.stderr)
    print(file=sys.stderr)
    emit(problems, ["url", "verdict", "coverage", "canonical", "lastCrawl", "fetch", "robots",
                    "richResults"], args)


def sitemaps(args):
    rows = []
    for s in call(f"/sites/{site_path(args.site)}/sitemaps").get("sitemap", []):
        counts = s.get("contents", [{}])[0]
        rows.append({"path": s["path"], "type": counts.get("type", ""),
                     "submitted": counts.get("submitted", 0), "indexed": counts.get("indexed", 0),
                     "lastDownloaded": s.get("lastDownloaded", "never")[:10],
                     "errors": s.get("errors", 0), "warnings": s.get("warnings", 0),
                     "pending": s.get("isPending", False)})
    emit(rows, ["path", "type", "submitted", "indexed", "lastDownloaded", "errors",
                "warnings", "pending"], args)


def canonical_state(idx):
    """Google's chosen canonical vs the page's declared one.

    An absent userCanonical means the page ships no canonical tag -- untidy, but Google
    self-canonicalises fine. Only a genuine disagreement is worth acting on.
    """
    g, u = idx.get("googleCanonical"), idx.get("userCanonical")
    if not u:
        return "self (none declared)"
    if g == u:
        return "self"
    return f"MISMATCH -> {g}"


def sitemap_urls(site):
    """Every <loc> in the property's sitemaps, following sitemap indexes one level."""
    import xml.etree.ElementTree as ET
    NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    seen, out = set(), []
    queue = [s["path"] for s in call(f"/sites/{site_path(site)}/sitemaps").get("sitemap", [])]
    while queue:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            root = ET.fromstring(urllib.request.urlopen(sm, timeout=30).read())
        except Exception as e:
            print(f"! could not read {sm}: {e}", file=sys.stderr)
            continue
        if root.tag.endswith("sitemapindex"):
            queue += [n.text.strip() for n in root.iter(f"{NS}loc")]
        else:
            out += [n.text.strip() for n in root.iter(f"{NS}loc")]
    return out


def inspect_one(url, site):
    """One URL Inspection call. ~10s of API latency each, hence the pool in inspect()."""
    req = urllib.request.Request(
        "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
        data=json.dumps({"inspectionUrl": url, "siteUrl": site,
                         "languageCode": "en-US"}).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=120).read())["inspectionResult"]
    except urllib.error.HTTPError as e:
        return {"url": url, "verdict": f"ERROR {e.code}", "coverage": e.read().decode()[:120]}
    except Exception as e:
        return {"url": url, "verdict": "ERROR", "coverage": str(e)[:120]}
    idx = res.get("indexStatusResult", {})
    return {"url": url, "verdict": idx.get("verdict", ""),
            "coverage": idx.get("coverageState", ""),
            "canonical": canonical_state(idx),
            "lastCrawl": (idx.get("lastCrawlTime") or "")[:10],
            "fetch": idx.get("pageFetchState", ""),
            "robots": idx.get("robotsTxtState", ""),
            "richResults": res.get("richResultsResult", {}).get("verdict", "NONE")}


def inspect(args, _return=False):
    """URL Inspection. Quota: 2000 URLs/day, 600/minute."""
    urls = list(args.urls)
    if getattr(args, "sitemap", False):
        urls += sitemap_urls(args.site)
    urls = list(dict.fromkeys(urls))
    if not urls:
        sys.exit("give URLs or --sitemap")
    if len(urls) > 2000:
        print(f"! {len(urls)} URLs exceeds the 2000/day quota; inspecting the first 2000",
              file=sys.stderr)
        urls = urls[:2000]

    token()  # mint once up front so workers never race on it
    rows, done = [], 0
    # 8 workers keeps us well under 600/min while cutting a 105-URL sweep from ~20min to ~3.
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for r in pool.map(lambda u: inspect_one(u, args.site), urls):
            rows.append(r)
            done += 1
            if len(urls) > 10 and done % 10 == 0:
                print(f"  ...{done}/{len(urls)}", file=sys.stderr)
    if _return:
        return rows
    emit(rows, ["url", "verdict", "coverage", "canonical", "lastCrawl", "fetch", "robots",
                "richResults"], args)


def snapshot(args):
    """Append a dated full pull to data/ so we build history the UI can't give us."""
    start, end = window(args)
    DATA.mkdir(exist_ok=True)
    for dims in (["date", "query"], ["date", "page"], ["date", "page", "query"]):
        name = "_".join(d for d in dims if d != "date")
        rows = fetch_all(args.site, {"startDate": start, "endDate": end,
                                     "dimensions": dims, "dataState": "final"})
        f = DATA / f"{name}.jsonl"
        with f.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(dict(zip(dims, r["keys"]), clicks=r["clicks"],
                                         impressions=r["impressions"], ctr=r["ctr"],
                                         position=r["position"])) + "\n")
        print(f"{f.name}: +{len(rows)} rows ({start}..{end})", file=sys.stderr)


def emit(rows, cols, args, meta=None):
    if meta:
        print(f"# {meta}", file=sys.stderr)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    if not rows:
        print("(no rows)", file=sys.stderr)
        return
    w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    # URLs and queries are the identity of a row -- truncating them can make two
    # different rows look like duplicates, so they get much more room than other columns.
    cap = {"url": 110, "page": 110, "query": 70}
    w = {c: min(v, cap.get(c, 40)) for c, v in w.items()}
    print("  ".join(c[:w[c]].ljust(w[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, ""))[:w[c]].ljust(w[c]) for c in cols))


def main():
    p = argparse.ArgumentParser(prog="gsc", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", default=SITE,
                   help="property URL; defaults to $GSC_SITE")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output (accepted before or after the subcommand)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_window(sp, days=28):
        sp.add_argument("--start"); sp.add_argument("--end")
        sp.add_argument("--days", type=int, default=days)
        sp.add_argument("--type", default="web",
                        choices=["web", "image", "video", "news", "discover", "googleNews"])
        sp.add_argument("--data-state", default="final", choices=["final", "all"])

    sp = sub.add_parser("sites", help="list properties this account can read")
    sp.set_defaults(func=lambda a: emit(call("/sites")["siteEntry"],
                                        ["siteUrl", "permissionLevel"], a))

    sp = sub.add_parser("q", help="search analytics")
    add_window(sp)
    sp.add_argument("--dims", default="query", help="query,page,country,device,date,searchAppearance")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--filter", action="append",
                    help="dim:operator:expression e.g. page:contains:/blog")
    sp.set_defaults(func=query)

    sp = sub.add_parser("compare", help="window vs previous window, by click delta")
    add_window(sp)
    sp.add_argument("--dim", default="query")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--min-clicks", type=int, default=10)
    sp.add_argument("--worst", action="store_true", help="biggest losers first")
    sp.add_argument("--filter", action="append", help="dim:operator:expression")
    sp.set_defaults(func=compare)

    sp = sub.add_parser("sitemaps", help="sitemap status and index counts")
    sp.set_defaults(func=sitemaps)

    sp = sub.add_parser("inspect", help="URL inspection (2000/day quota)")
    sp.add_argument("urls", nargs="*")
    sp.add_argument("--sitemap", action="store_true", help="inspect every URL in the sitemaps")
    sp.add_argument("--workers", type=int, default=8)
    sp.set_defaults(func=inspect)

    sp = sub.add_parser("audit", help="inspect all sitemap URLs, report only problems")
    sp.add_argument("--workers", type=int, default=8)
    sp.set_defaults(func=audit)

    sp = sub.add_parser("sitemap-submit", help="submit a sitemap to Google (write)")
    sp.add_argument("urls", nargs="+")
    sp.set_defaults(func=sitemap_submit)

    sp = sub.add_parser("sitemap-delete", help="unsubmit a sitemap (write)")
    sp.add_argument("urls", nargs="+")
    sp.set_defaults(func=sitemap_delete)

    sp = sub.add_parser("snapshot", help="append a pull to data/ to build history")
    add_window(sp, days=7)
    sp.set_defaults(func=snapshot)

    # Pulled out before parsing so it works on either side of the subcommand;
    # argparse alone would only accept a global flag before it.
    argv = sys.argv[1:]
    want_json = "--json" in argv
    args = p.parse_args([a for a in argv if a != "--json"])
    args.json = want_json

    if not args.site:
        sys.exit(
            "No property configured. Set GSC_SITE, or pass --site.\n\n"
            "  export GSC_SITE='https://www.example.com/'   # URL-prefix property\n"
            "  export GSC_SITE='sc-domain:example.com'      # Domain property\n\n"
            "Run `gsc --site <any> sites` to list the properties you can read.")
    args.func(args)


if __name__ == "__main__":
    main()
