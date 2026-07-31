# release_check

Lists releases on Deezer, by artists already in your Navidrome library, that you
appear not to own. One command, results printed to the terminal, newest first.

```text
RELEASE DATE  ARTIST          TYPE     TITLE            URL

Albums (1)
2026-06-02    Artist Name     Album    Album Title      https://www.deezer.com/album/302127

EPs (1)
2026-07-18    Another Artist  EP       Another Release  https://www.deezer.com/album/825535241

Singles (1)
2026-07-24    Artist Name     Single   Release Title    https://www.deezer.com/album/14894641

42 missing releases: 8 albums, 6 EPs, 28 singles
5 artists could not be resolved
3 releases require review
```

## What it does

- Reads your artists, albums and tracks from Navidrome over its Subsonic API.
- Finds each artist on Deezer, corroborating the match against your album titles.
- Pulls each artist's full Deezer discography, handling pagination.
- Works out which releases are not in your library, being careful about
  reissues, deluxe editions and singles whose tracks you already own.
- Prints the result sorted globally by release date, newest first.

## What it does not do

It does not download music, decrypt anything, or touch your Deezer account. It
is not a daemon, a service, a web app or a downloader — it runs when you run it
and then exits. It never writes to Navidrome: no scans, ratings, favourites,
playlists or tag edits. Your music files are never opened.

It does not use MusicBrainz, in any form, directly or through a dependency.
Deezer's own catalogue is the only metadata source.

## Requirements

- Python 3.10 or newer (macOS ships 3.9; `brew install python` if `python3 -V`
  reports anything older).
- No third-party runtime dependencies. The standard library covers HTTP, JSON
  and SQLite.
- Network access from your Mac to Navidrome, and to `api.deezer.com`.

## Install

```bash
./install.sh
```

That builds an isolated environment under `~/.local/share/release-check`, copies
the application into it, and links the `release-check` command into
`~/.local/bin`. Nothing is added to your system Python, and **the source
directory is not needed afterwards** — move it or delete it and the command
keeps working.

Then:

```bash
release-check setup     # enter your Navidrome details; it tests the connection
release-check           # list missing releases
```

Re-run `./install.sh` any time to upgrade in place. To remove it:

```bash
./install.sh --uninstall
```

Settings and cache are left alone by `--uninstall`; it tells you how to remove
those too if you want a complete wipe.

If `~/.local/bin` is not on your `PATH`, the installer says so and prints the
one line to add.

<details>
<summary>Running from the source directory instead</summary>

No install needed — `python3 release_check.py` takes exactly the same
arguments, and reads `.env` from the project directory if one is there.

```bash
python3 release_check.py setup
python3 release_check.py
```

</details>

## Where things live

| | |
|---|---|
| Application | `~/.local/share/release-check/` |
| Command | `~/.local/bin/release-check` |
| Settings | `~/.config/release_check/.env` (mode 600) |
| Cache and mappings | `~/.local/state/release_check/state.sqlite3` |

All absolute, so it behaves the same from any directory.

### Changing settings later

Re-run `release-check setup` to walk through everything again — it offers your
current values as defaults, so pressing Enter keeps them.

For a single change:

```bash
release-check config list                    # every setting and where it came from
release-check config set url http://your-server:4533
release-check config set timeout 30
release-check config password                # prompted, never echoed
release-check config unset timeout           # back to the default
release-check config path                    # where the file lives
```

`config list` shows provenance, which is what you want when a change appears
not to take effect:

```text
url            http://your-server:4533  (/Users/you/.config/release_check/.env)
username       branden            ($NAVIDROME_URL style: environment)
password       ********           (/Users/you/.config/release_check/.env)
timeout        20                 (default)
```

The password is never accepted as a command-line argument — it would land in
your shell history and be visible to `ps`. `config password` prompts for it
instead. Everything is stored in a plain, hand-editable file at mode 600.

Settings are: `url`, `username`, `password`, `timeout`, `cache-path`,
`cache-max-age`, `workers`.

`NAVIDROME_URL` is the base URL only — `/rest` is appended for you. Any host,
port or path works; `your-server` is resolved through Tailscale exactly as your
browser would. This tool never installs or configures Tailscale.

Settings are read from the first source that has them:

1. Real environment variables
2. `--env-file PATH`, or `$RELEASE_CHECK_ENV`
3. `./.env` in the current directory
4. `~/.config/release_check/.env`

When nothing is found, the error lists every location it searched. Environment
variables win over the file, and both `setup` and `config set` warn you when one
is shadowing what you just saved.

### Checking the connection

```bash
release-check check
```

That validates the URL, connects, authenticates, and confirms the endpoint is
Subsonic-compatible. If something is wrong it says which thing:

| Message | Meaning |
|---|---|
| Could not resolve the hostname | DNS/MagicDNS cannot find `your-server` |
| Connection refused | Host is up, nothing listening on that port |
| No route to host | Host is offline or off the tailnet |
| Connection timed out | Host is unreachable or asleep |
| TLS certificate verification failed | HTTPS certificate problem |
| Navidrome rejected the credentials | Username or password is wrong |
| ... is not a Subsonic-compatible API | URL points at something else |
| Navidrome returned 404 | URL path is wrong |

Network failures and credential failures are never conflated.

## Running a scan

```bash
release-check
```

That is the whole normal workflow. Results go to stdout; warnings, unresolved
artists and the review section go to stderr, so `release-check > missing.txt`
gives you a clean file.

Useful flags:

```bash
release-check --since 2024            # only recent releases
release-check --type album --type ep  # skip singles
release-check --artist "Björk"        # one artist
release-check --flat                  # no type groups, pure date order
release-check --refresh               # ignore the cache, refetch
release-check -v                      # progress detail on stderr
```

Each result line ends with the release's Deezer URL, so the output composes with
whatever you want to do next.

While a scan runs, findings appear on stderr as each artist is resolved:

```text
[47/196] Fleetwood Mac — 3 missing (2 albums, 1 single)
[52/196] Ghost — unresolved, 2 candidates
[58/196] Alabama — 12 missing (4 albums, 8 singles), 3 to review
[61/196] Genesis
```

Artists with nothing new stay off the scroll, so the list stays dense. These are
summaries, not the report — the sorted table still arrives at the end, on
stdout. Nothing about the live view touches stdout, so `release-check > out.txt`
is unaffected. `--no-progress` turns it off, and it is skipped automatically
when stderr is not a terminal.

Running from a checkout instead? `python3 release_check.py` and
`python3 -m release_check` take exactly the same arguments.

### Local state

Cached API responses, artist mappings and ignore lists live in one SQLite file
at `~/.local/state/release_check/state.sqlite3` (override with `CACHE_PATH`). It is
an absolute path, so it is shared no matter where you run the command from.
Cached entries expire after 24 hours by default (`CACHE_MAX_AGE_HOURS`) — but
not all of them. An album's metadata and track list cannot change once the
album is out, so those are kept for 30 days; discography listings and artist
searches, which exist precisely to change, use the configured lifetime. That is
what makes a second run cheap. `--refresh` overrides both.

```bash
release-check cache                  # where it is, how old it is
release-check cache --clear          # drop cached API responses
release-check cache --reset-mappings # drop artist mappings
```

Clearing the cache never deletes your manual mappings, and a failed refresh
never discards data that is already cached.

### Resolving ambiguous artists

Artists that cannot be matched confidently are listed after a scan and never
guessed at. To see them again:

```bash
release-check artists
```

```text
Ghost: several Deezer artists share this name and none matches the local albums
    Ghost  deezer id 1160651  (1,234,567 fans)
    Ghost  deezer id 4859761  (2,145 fans)
```

Work through them interactively:

```bash
release-check resolve
```

```text
Algorhythm
  7 Deezer artists share this name with equal catalogue overlap
  1. Algorhythm  [id 461342]  427 fans
       Illusion
       Island
       Time And Space
  2. Algorhythm  [id 324774101]  34 fans
       Illusion
       Make It Last
       Island
  number(s) to map  [s]kip  [i]gnore  [d] enter an id or URL  [q]uit
  >
```

Each candidate lists a few album titles, with any that match your library shown
first — which Ghost is yours is answerable from a track listing and almost never
from a fan count.

Your options per artist:

| Answer | Effect |
|---|---|
| `1` | Map to that Deezer artist |
| `1 2` | Map to **both** — their discographies are merged |
| Enter, or `s` | Leave it unresolved; you will be asked again next time |
| `i` | Ignore permanently, never reported again |
| `c` | Clear everything known about this artist, back to unresolved |
| `d 1160651` or a Deezer URL | Use an ID you found yourself |
| `q` | Stop here, keeping what you have already decided |

**Selecting several is the interesting one.** Deezer routinely splits one act
across duplicate artist entries, each holding part of the catalogue — the two
Algorhythm entries above share *Illusion* and *Island*. Mapping both merges
their discographies and de-duplicates the overlap, so you get complete coverage
without double entries.

The same things are available non-interactively:

```bash
release-check map "Ghost" 1160651              # one
release-check map "Ghost" 1160651 4859761      # several, merged
release-check ignore "Karaoke Hits Vol 3"
release-check unmap "Ghost"                    # clear, back to unresolved
release-check artists --mappings               # list what is saved
```

Mappings persist between runs and always win over automatic matching. A normal
scan is never interactive.

## How matching works

**Artists.** Names are compared after folding case, accents, apostrophe styles,
`&`/`and`, and punctuation, plus a leading-article variant so "The Beatles"
matches "Beatles". Names are never split on `&`, `and`, `feat.`, `with`, `vs.`
or commas, because those appear inside real names. A name match alone is not
enough: candidates are corroborated against your album titles, and the one that
actually shares releases with your library wins. This matters — searching Deezer
for "Björk" returns "Björk & Toffe" as the *first* result. Karaoke and tribute
acts are filtered out. When several same-named artists remain and none matches
your albums, the artist is reported unresolved rather than guessed.

**Releases.** Titles are decomposed into a base title, cosmetic edition markers
and meaningful version markers. Edition markers are ignored when deciding
identity — "Deluxe Edition", "2011 Remaster", "20th Anniversary", "Bonus Track
Version", "Explicit", territorial editions. Version markers are preserved,
because they denote a different recording — "Live", "Acoustic", "Remix", "Radio
Edit", "Instrumental", "Demo", "Mono", "Alternate Take", language versions. So a
remaster of an album you own is not reported, but a live version of it is.

Beyond titles, releases are compared by recording coverage: each Deezer track is
looked up against every track you own by that artist, matching on title, version
marker and duration (±5 s, which absorbs encoder padding without hiding a
genuinely different edit).

**Singles** get this treatment specifically. A single whose only track is
already on an album you own is not reported. A single carrying an exclusive
B-side, a remix, an acoustic take, or a materially different edit *is* reported.
Where Deezer supplies ISRCs for both a single and an album you own, identical
ISRCs prove identical recordings and the single is suppressed. Where recording
identity cannot be established, the single goes to the review section instead of
being asserted as missing.

**Duplicates.** Territorial variants, explicit/clean pairs, reissues and repeated
product listings are collapsed to one canonical entry — the earliest dated one,
since Deezer duplicates are usually reissues of a single original product. A
deluxe edition whose extra tracks you already own is treated as owned. Live and
studio versions are never merged, and a single is never merged into an album of
the same name.

**Verdicts.** Every release lands on one of: owned, probably owned, missing,
probably missing, ambiguous, or ignored. Only *missing* and *probably missing*
reach the main list. *Ambiguous* goes to the review section on stderr. The bias
is deliberate: a release you actually own being quietly filtered out is a much
smaller problem than being told to go buy something twice.

### Sorting and grouping

Results are grouped by release type — Albums, then EPs, then Singles, then
Unclassified — and within each group sorted descending by release date, newest
first, across all artists. They are never grouped by artist.

The `TYPE` column is kept even though the group heading repeats it, so every
line stays self-describing when the output is piped somewhere.

For one continuous list sorted purely by date, with no group headings:

```bash
release-check --flat
```

Deezer sometimes gives partial dates (`2019-00-00`), so precision is tracked and
used as the tiebreak: within the same period, a less precise date sorts *below*
the fully dated releases it overlaps.

```text
2024-06-15   full date
2024-06      month only, below every dated June release
2024-01-01   full date
2024         year only, below every dated 2024 release
unknown      always last
```

Remaining ties break alphabetically by artist then title, so repeated runs print
identically.

## Deezer access

**This tool uses `https://api.deezer.com` — Deezer's public, unauthenticated
catalog API. Classification: public and documented by Deezer, but not covered by
any stability guarantee, and usable without an account.**

It needs no credentials at all, and never sends any. Artist search, full
discographies with pagination, album metadata (UPC, label, contributors, track
counts) and complete track listings with ISRCs are all available anonymously,
so there is no Deezer account setup to do and nothing to keep up to date.

Nothing here logs in to Deezer, touches a browser profile, or reads cookies.

No third-party Deezer library is used, so there is nothing to audit for hidden
downloading or decryption behaviour.

All Deezer access is isolated in `src/release_check/deezer.py`. Matching,
classification and reporting only see plain dataclasses, so the provider could
be replaced without touching them.

### Rate limits

Deezer allows roughly 50 requests per 5 seconds per IP (measured: 60 concurrent
requests yielded 49 successes and 11 quota errors). Requests are limited to 8/s
with a small burst, quota errors are retried three times with exponential
backoff and jitter, and everything is cached. Detail is only fetched for
releases that still look missing after a cheap first pass, so a second run over
the same library costs almost nothing.

## Security

- Credentials come from `.env` (git-ignored) or the environment; real
  environment variables win. Nothing is ever written into source, tests or logs.
- Navidrome authentication uses Subsonic's salted-token scheme with a fresh salt
  per request, so your password never appears in a URL, a proxy log or a
  traceback.
- Secrets are wrapped in a `Secret` type that redacts itself in `str()`,
  `repr()`, f-strings, logging and tracebacks, and refuses to be pickled or
  JSON-serialised.
- A logging filter scrubs known secret values and credential-shaped URL
  parameters from every log record, including at `-vv` debug level.
- `.env` is checked at startup and you are warned if it is readable by other
  local accounts. The state database is created mode 600.
- The state file never contains credentials of any kind.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unexpected failure |
| 2 | Command-line usage error |
| 3 | Configuration problem |
| 4 | Navidrome connection or protocol problem |
| 5 | Navidrome rejected the credentials |
| 6 | Deezer problem |
| 7 | Completed, but partially — some artists or albums failed |

Code 7 still prints results; the summary says the output is partial. One failing
artist never stops the rest of the scan.

## Tests

```bash
python3 -m pytest        # or: python3 -m pytest -q
```

392 tests, no network access, no real credentials, both APIs mocked. They cover
name and title normalization, edition versus version markers, deluxe/expanded/
remaster matching, track overlap, the singles rules, alternate-version
detection, duplicate collapsing, ambiguous artist results, manual mappings,
cache expiry, partial Navidrome and Deezer failures, the connection error
taxonomy, credential redaction, reverse-chronological sorting with
incomplete dates, and terminal formatting.

## Known limitations

- **Deezer is the whole world.** An artist or release absent from Deezer cannot
  be reported, and a release Deezer lists for the wrong artist may surface. If
  your library is heavy on small labels or non-Western catalogues, expect more
  unresolved artists.
- **Local files have no ISRCs.** The Subsonic API does not expose them, so
  recording identity against your library rests on title, version marker and
  duration. ISRCs are used only Deezer-side, where both a single and an owned
  album expose them.
- **Duration tolerance is a heuristic.** ±5 seconds distinguishes a different
  edit from encoder padding most of the time, not always. Sources that differ by
  a long fade may be flagged as different recordings.
- **Compilations.** A greatest-hits collection whose every track you already own
  is treated as owned and suppressed. If you want to be told about the product
  itself, that is the wrong call for you.
- **"Various Artists"** and similar placeholder artists are skipped entirely.
- **Deezer's `record_type` is inconsistent.** Track count and total duration are
  used to correct obvious mislabels, but an EP/album boundary case can land on
  either side; genuinely undecidable cases print as `Unknown`.
- **Year-gap heuristic.** An identical title more than a year from your tagged
  year, with no edition marker, is treated as ambiguous rather than owned. If
  your year tags are release-date-of-pressing rather than original release, this
  will put more in the review section.
- **First run is slow.** A large library means one Deezer search plus a
  discography per artist, and two more requests per candidate release, against
  an ~8/s rate limit. `--since` is the effective lever, and live progress shows
  findings as they arrive. Later runs are served from cache.
