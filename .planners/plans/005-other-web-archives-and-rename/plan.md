---
id: 5
slug: other-web-archives-and-rename
status: draft
branch:
created: 2026-11-06T23:06:03-08:00
concluded:
pr:
---

# Survey other web archives and rename the package for more than one

## Plan

### Goal

The package looks up captures in one archive, the Internet Archive's
Wayback Machine. When it has none, a caller has nowhere else to look. In
one downstream run over a few hundred news-article URLs, about a third had
no Wayback capture at all, mostly regional papers and repost sites. This
plan answers three things before any code changes:

1. **Which other archives** can be searched for a URL by a well-behaved
   client, and what each one returns.
2. **Which existing tools** already do this, in Python or not, and whether
   this package should adopt, wrap, or defer to one of them rather than
   grow.
3. **Whether to rename the package** to something that is not one
   archive's name, if it does grow. `wayback` is already taken on PyPI
   (by EDGI's Internet Archive client), so the current name could never
   be published anyway.

The deliverable is two sidecars and a decision: `archives-survey.md` and
`tools-survey.md` in this plan's directory, then a recommendation in the
Log that the user accepts or redirects. Implementation, if any, goes to
follow-up plans.

### a. Archives to survey

For each archive, record:

- **Lookup:** Memento TimeMap (RFC 7089), CDX, JSON, or none.
- **Auth:** none, a key, or an account.
- **Limits:** published rate limits and terms on automated access.
- **Raw copy:** whether there is a capture as fetched, without link
  rewriting (the Wayback `id_` form), or only a rendered replay.
- **Status code:** whether a capture's original HTTP status is available.
- **Coverage:** which sites and languages.
- **Operator:** who runs it, and how stable its domains are.
- **Probe:** the result of one probe, with a public example URL (for
  example `https://example.com/` or a long-lived encyclopedia article),
  one request per question, with an honest User-Agent.

Candidates. Every claim here is from memory and is to be verified:

| Archive | Expected lookup | Notes to check |
|---|---|---|
| archive.today (`archive.ph`, `.is`, `.md`, and other mirrors) | Memento TimeMap at `/timemap/<url>`; no documented API | Known to challenge automated clients (CAPTCHA). Anonymous operator, rotating domains. Stores a rendered page and a screenshot, so it may have no raw copy |
| Memento aggregators: Time Travel (LANL), MemGator (self-hostable, open source) | One TimeMap query across many archives | Which archives they include, freshness, limits; MemGator could run locally |
| Arquivo.pt | Documented CDX and TimeMap APIs | Portuguese national archive with international crawls; terms of use |
| Library of Congress web archives | Memento | Collection-scoped (elections, events); coverage of news |
| UK Web Archive | Memento / CDX | Many captures viewable only on library premises |
| Archive-It collections | Through the Wayback Machine | Whether its captures already appear in the Wayback CDX index |
| Common Crawl | CDX index server per crawl; WARC records on public storage | Raw HTML by byte-range request; broad but sampled coverage, not on demand |
| Perma.cc | API with a key | Captures only what its users save; mostly legal and academic citations |
| Ghostarchive, and others found in the survey | Unknown | Whether any lookup exists at all |
| Search engine caches | — | Google's cache ended in 2024; record as unavailable, with date |

An archive that blocks automated clients is still useful, but not as a
lookup: the package can return a link for a person to open (for example
archive.today's "newest" URL form) without fetching it. The package never
solves CAPTCHAs or routes around blocks.

### b. Existing tools to survey

For each tool, record:

- language and license;
- the archives it covers;
- what it does: lookup, fetch, replay, or save;
- last release and whether it is maintained;
- its dependency weight;
- whether its pacing and error handling match plan 004's.

Candidates. Again from memory and to be verified:

| Tool | What it is believed to do |
|---|---|
| EDGI `wayback` (PyPI `wayback`) | Internet Archive CDX and playback client with rate limits set with IA staff; one archive |
| `waybackpy` | Internet Archive CDX, availability, and Save Page Now; this package's current dependency |
| `cdx_toolkit` | One CDX interface over Common Crawl and the Internet Archive |
| `memento_client` (LANL) | Python Memento client, TimeGate and TimeMap, works with aggregators |
| MemGator | Go Memento aggregator, CLI or server, configurable archive list |
| `archivenow` (ODU Web Science and Digital Libraries group) | Pushes a URL to several archives (saving, not lookup) |
| `archiveis` and similar | archive.today submission wrappers |
| `waybackpack`, `wayback-machine-downloader` | Bulk download of one site's Wayback captures |
| `pywb`, `warcio` | Replay and WARC tooling, for running an archive rather than querying one |

The question for each: does it already cover "given a URL and a date
window, find the best capture in any archive, and give its raw copy"? If
one tool does, the recommendation may be to depend on it, or to stop
growing this package and point callers to that tool, instead of a rename.

### c. Rename

Only if the survey recommends that the package cover more than one
archive. The things to settle:

- **Name.** `webarc` is the working proposal. Checked on PyPI 2026-11-06:
  - free: `webarc`, `webarchives`, `web-archives`, `archive-lookup`
  - taken: `wayback`, `webarchive`, `memento`, `mementos`,
    `timetravel`, `archivist`

  Check again before deciding, and check GitHub for repositories of the
  same name.
- **Distribution and import names.** Both change. Keep a `wayback`
  import shim that re-exports with a `DeprecationWarning` for one
  release, or make a clean break?
- **Repository.** Renaming on GitHub redirects old clone and fetch URLs,
  so callers pinning `git+https://github.com/OWNER/wayback@vX.Y.Z` keep
  working until a new repository takes the old name. The README and
  CHANGELOG say how to repin.
- **Identifiers that carry the name:**
  - the default User-Agent (`wayback/<version>`)
  - the `wayback` logger
  - the deprecated `save_log` default path (`data/wayback-runs.jsonl`)
  - the `function` field in events
  - the template's project metadata
- **Publishing.** The new name could go to PyPI, which `wayback` never
  could (publishing is gated by the template's `PUBLISH_ENABLED`).

### Order

1. Plan 004 (CDX refusals and rate limits) lands first or alongside,
   since it reshapes the request path any second archive would share.
2. Survey the tools (b) before the archives (a): an existing tool may
   answer most of (a).
3. Survey the archives (a).
4. Write the recommendation in the Log: adopt a tool, extend this package
   to more archives, or stay Wayback-only.
5. Get the user's decision. Then, if it grows, rename (c) and the
   multi-archive lookup become follow-up plans, the rename first, so new
   code lands under the new name.

### Risks

- **Claims from memory.** Every table row above is unverified until the
  survey checks it against the tool's or archive's own documentation.
- **Terms of use.** Some archives forbid or discourage automated access.
  The survey records the terms. An archive whose terms rule out
  automated lookup is listed as manual-link-only, not used.
- **Probes are requests to real services.** Keep them to a handful per
  service, paced, with an honest User-Agent, and never with a caller's
  real URLs.
- **A rename breaks callers' imports.** The shim (or a clear CHANGELOG
  entry) and the GitHub redirect limit that, but cannot remove it.

### Out of scope

- Saving pages to any archive (Save Page Now, archive.today submission).
- Running an archive or replay service.
- Solving CAPTCHAs, rotating addresses, or any way around an archive's
  limits or blocks.
- Implementing lookups in a second archive (follow-up plans after the
  decision).

## Handoff

**State (2026-11-06):** draft, nothing surveyed.

**Questions:**

1. Name, if the package grows: `webarc`, or another (free on PyPI on
   2026-11-06: `webarchives`, `web-archives`, `archive-lookup`)?
2. Rename the GitHub repository too, or only the distribution and import
   names?
3. Keep a `wayback` import shim for one release, or make a clean break?
4. Publish to PyPI under the new name once renamed?
5. If an existing tool covers the need, is adopting it (and retiring or
   freezing this package) an acceptable outcome?
