# Setup

Goal: Claude Code (or any agent, or you in a shell) reads and writes Google Search
Console for a property, headlessly, with **no key file anywhere on disk**.

The underlying mechanism is identical for every company. Only four values change.
Fill in this table and the rest is copy-paste:

| # | Value | Example | Where it comes from |
|---|---|---|---|
| 1 | GCP project id | `acme-growth` | Any GCP project. Create one if there isn't one; it holds nothing but the service account. |
| 2 | Service account email | `gsc-bot@acme-growth.iam.gserviceaccount.com` | You create it in step 3. Name is yours to pick. |
| 3 | Your own Google account | `you@acme.com` | The login that will impersonate the service account. |
| 4 | Property, exact spelling | `https://www.acme.com/` or `sc-domain:acme.com` | Search Console. **Copy it, don't retype it** — see the note below. |

Plus one thing you cannot get yourself: **someone who is an Owner of the Search
Console property** has to add the service account as a user (step 4). That is the
only step with no API and the only one that can block you. Line it up first.

Budget fifteen minutes, most of it waiting on Google's UI.

> **The property spelling is the single most common setup failure.** A Domain
> property is `sc-domain:acme.com` — no scheme, no slash. A URL-prefix property is
> the full origin *with* a trailing slash, and `https://www.acme.com/` and
> `https://acme.com/` are two different properties. Copy the value out of Search
> Console rather than typing what you think it is.

## What you end up with

```
you / Claude Code
      │
      ├─ ./gsc.py q --dims query --days 28
      │
      ├─ gcloud auth print-access-token   ← short-lived token, minted per process
      │        (impersonating a service account, or just your own login)
      │
      └─ Search Console API for one property
```

The credential is a token that expires in an hour. There is no JSON key to rotate,
leak, or commit. That is the main reason this exists in preference to the usual
service-account-key-in-a-file setup.

## The whole thing, in one block

For when you have done this before. Each step is explained below.

```sh
PROJECT=acme-growth
SA_NAME=gsc-bot
ME=you@acme.com
SITE='https://www.acme.com/'          # or 'sc-domain:acme.com'

gcloud auth login
gcloud config set project "$PROJECT"
gcloud services enable searchconsole.googleapis.com

gcloud iam service-accounts create "$SA_NAME" --display-name "Search Console bot"

SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member "user:$ME" --role roles/iam.serviceAccountTokenCreator

echo "Now add $SA as a user on $SITE in the Search Console UI (step 4)."

export GSC_SITE="$SITE"
export GSC_SA="$SA"
./gsc.py sites                         # should list the property
```

## 1. Install gcloud

```sh
brew install --cask google-cloud-sdk     # macOS
# or https://cloud.google.com/sdk/docs/install
gcloud auth login
```

## 2. Choose your auth path

**Path A — your own Google login.** Simplest. Good for one person on one machine.
You already did it in step 1. Skip to step 4 and leave `GSC_SA` unset.

**Path B — impersonate a service account, keylessly.** Use this when an agent, a CI
job, or more than one person needs access, or when access must survive someone
leaving. Still no key file: your login is the credential, and it *borrows* the
service account's identity. The rest of this section is Path B.

**Path C — a service account key file.** For **headless machines** — a VPS, a
container, CI. Path B cannot work there: keyless impersonation needs a human login
to impersonate *from*, and a server has no browser to log one in with. So a
headless box has to hold a credential of its own.

Given that, a *scoped service-account key* is the safest thing to put there — much
safer than running `gcloud auth login` on the box, which would leave a refresh
token for your entire Google account (Gmail, Drive, all of GCP) sitting on an
internet-facing machine. A key for a purpose-built service account grants exactly
one property's Search Console data and nothing else.

Path C needs neither gcloud nor any Python package on the target machine — just
`python3` and `openssl`. See [Headless machines](#headless-machines-vps-ci-containers)
below.

## 3. Create the service account and let yourself impersonate it

```sh
gcloud config set project YOUR_PROJECT

gcloud iam service-accounts create gsc-bot \
  --display-name "Search Console bot"

# The account whose gcloud login will mint tokens *as* the service account.
gcloud iam service-accounts add-iam-policy-binding \
  gsc-bot@YOUR_PROJECT.iam.gserviceaccount.com \
  --member "user:you@yourcompany.com" \
  --role roles/iam.serviceAccountTokenCreator
```

`serviceAccountTokenCreator` is what makes the keyless flow work: your login is
allowed to ask Google for a short-lived token belonging to the service account.

Enable the URL Inspection API. Nothing else needs enabling — see the two-hosts note
in the README:

```sh
gcloud services enable searchconsole.googleapis.com
```

## 4. Grant the account access to the property

This part has no API. It is a manual step in the Search Console UI, once:

1. Open [Search Console](https://search.google.com/search-console) → your property.
2. **Settings → Users and permissions → Add user.**
3. Paste the service account email (or the Google account, for Path A).
4. Permission: **Full** if you want `sitemap-submit` / `sitemap-delete`;
   **Restricted** is enough for every read command.

Only a property *Owner* can add users. If you are not one, whoever is has to do
this step.

> Verifying the property itself is a separate, earlier thing. If the property does
> not exist yet, add it in Search Console first (DNS record for a Domain property,
> or any of the usual methods for a URL-prefix one).

## 5. Configure this CLI

```sh
export GSC_SITE='https://www.example.com/'          # URL-prefix — trailing slash matters
# export GSC_SITE='sc-domain:example.com'           # Domain property instead
export GSC_SA='gsc-bot@YOUR_PROJECT.iam.gserviceaccount.com'   # omit for Path A
```

Put those in your shell profile, or in a `.envrc` if you use direnv. `GSC_SITE`
must match what Search Console shows, exactly — a Domain property is
`sc-domain:example.com`, not a URL.

Verify:

```sh
./gsc.py sites
```

You should see the property and your permission level. If you see an empty list,
step 4 has not taken effect — it is occasionally a few minutes slow.

## 6. Hand it to Claude Code

Two ways, and the first is usually enough.

### Just let it run the CLI

Claude Code already has a Bash tool. Drop this repo in the project (or anywhere on
PATH) and put the contract in `CLAUDE.md`:

```markdown
## Search Console

`./gsc-cli/gsc.py` reads and writes Search Console for this site. Run
`./gsc-cli/gsc.py --help` for the full command list, and read `gsc-cli/README.md`
before interpreting results — the delta sign conventions and the API's row limits
both cause silent misreadings.

Data lags ~3 days; windows already default to ending there.

`sitemap-submit` and `sitemap-delete` change what Google sees. Ask first.
```

That last line matters. Everything else is read-only, but those two are writes
against a live property.

### Pre-approve the read commands

So the agent is not stopped by a permission prompt on every call, in
`.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(./gsc-cli/gsc.py sites:*)",
      "Bash(./gsc-cli/gsc.py q:*)",
      "Bash(./gsc-cli/gsc.py compare:*)",
      "Bash(./gsc-cli/gsc.py sitemaps:*)",
      "Bash(./gsc-cli/gsc.py inspect:*)",
      "Bash(./gsc-cli/gsc.py audit:*)",
      "Bash(./gsc-cli/gsc.py snapshot:*)"
    ]
  }
}
```

Deliberately leaving `sitemap-submit` and `sitemap-delete` off the list, so those
still prompt.

## Headless machines (VPS, CI, containers)

`gcloud` is not needed here at all. `gsc.py` exchanges a service-account key for an
access token itself: it builds a JWT, signs it RS256 by handing the key to `openssl`
over a pipe, and POSTs it to Google's token endpoint. Requirements are `python3` and
`openssl`, both of which any Linux box already has.

### Make the service account as powerless as possible

Two decisions do most of the security work, and both are easy to get wrong:

- **Give it a dedicated service account, not a shared one.** If you reuse the
  account that runs your Cloud Functions or your data pipeline, its key on a VPS can
  act as all of those things. Create one whose only purpose is this.
- **Give it no GCP project roles at all.** Search Console permission is granted
  inside Search Console (step 4), not through IAM. The service account needs *zero*
  IAM roles on the project. Verify with:

  ```sh
  gcloud projects get-iam-policy YOUR_PROJECT \
    --flatten="bindings[].members" \
    --filter="bindings.members:gsc-bot@YOUR_PROJECT.iam.gserviceaccount.com" \
    --format="value(bindings.role)"
  ```

  Empty output is the goal. It means a leaked key exposes one property's search
  data and nothing else in your cloud.

Also prefer **Restricted** over **Full** in step 4 unless you actually need the
agent submitting sitemaps. Restricted is read-only: every analysis command works,
and `sitemap-submit` / `sitemap-delete` are refused. Sitemaps get refetched
automatically regardless.

### Install the key without it touching your laptop

Piping through SSH means the key is written once, on the target, and never lands on
your own disk where you would then have to remember to shred it:

```sh
gcloud iam service-accounts keys create /dev/stdout \
  --iam-account=gsc-bot@YOUR_PROJECT.iam.gserviceaccount.com \
| ssh YOUR_HOST 'umask 077; mkdir -p ~/.gsc && cat > ~/.gsc/key.json'
```

Then confirm the permissions are `600` on a `700` directory:

```sh
ssh YOUR_HOST 'chmod 700 ~/.gsc; chmod 600 ~/.gsc/key.json; ls -la ~/.gsc'
```

Keep the key **outside any git checkout**. `~/.gsc/` is deliberately not in the
repo, so no `.gitignore` mistake can ever commit it.

### Configure

```sh
export GSC_SITE='https://www.example.com/'
export GSC_KEY_FILE="$HOME/.gsc/key.json"
# GSC_SA is not used on this path -- the key identifies the account.
```

`GSC_KEY_FILE` takes precedence over gcloud when both are available.

### Rotating and revoking

Keys do not expire. Rotate by creating a new one, installing it, then deleting the
old:

```sh
gcloud iam service-accounts keys list --iam-account=SA_EMAIL --managed-by=user
gcloud iam service-accounts keys delete KEY_ID --iam-account=SA_EMAIL
```

If a box is compromised, deleting the key cuts it off immediately — which is the
other reason to give each machine its own dedicated account.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Could not mint a token via gcloud` | Not logged in, or missing `serviceAccountTokenCreator` on the service account. |
| `sites` returns an empty list | Step 4 not done, or done on a different property spelling. |
| `HTTP 403` on `inspect` only | `searchconsole.googleapis.com` not enabled. Search analytics does not need it. |
| `HTTP 403` on everything | Permission is on a different property, or `GSC_SITE` has the wrong shape. |
| `HTTP 400 … startDate` | A date outside the 16-month window Search Console retains. |
| Writes fail, reads work | Permission is **Restricted**. Sitemap writes need **Full**. |
| `Token exchange failed … invalid_grant` | The key was deleted, or the machine's clock is skewed — the JWT is time-signed. Check `timedatectl`. |
| `openssl could not sign the JWT` | `GSC_KEY_FILE` is not a service-account key JSON, or `openssl` is missing. |
