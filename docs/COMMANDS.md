# Command reference

Every command, flag and setting in `release-check`.

Running the command with no subcommand means `scan`, so `release-check`,
`release-check --since 2024` and `release-check scan --since 2024` are the same
thing.

---

## By task

| I want to… | Command |
|---|---|
| Configure it the first time | `release-check setup` |
| See what's configured, and where each value comes from | `release-check config` |
| Change one setting | `release-check config set <key> <value>` |
| Change the password | `release-check config password` |
| Confirm Navidrome is reachable | `release-check check` |
| List releases I don't own | `release-check` |
| List only recent ones | `release-check --since 2024-01` |
| List only albums | `release-check --type album` |
| Fix an artist matched to the wrong Deezer artist | `release-check resolve "<artist>"` |
| Work through artists the scan couldn't match | `release-check resolve` |
| Answer the "needs review" section from the last scan | `release-check review` |
| Dismiss one album from the results | `release-check block --album <url>` |
| Never report an artist again | `release-check block --artist "<artist>"` |
| Undo a block | `release-check unblock --artist "<artist>"` |
| See what I've mapped and blocked | `release-check artists --mappings` |
| Force fresh data from Deezer | `release-check --refresh` |

---

## Setup and configuration

### `setup`

```bash
release-check setup
```

Guided first-run configuration. Prompts for URL, username and password, tests
the connection before saving, and retries up to three times if the connection
fails. Writes to `~/.config/release_check/.env` at mode 600. Press Enter at any
prompt to keep the existing value.

### `config`

```bash
release-check config                      # show every setting and its source
release-check config path                 # print the settings file location
release-check config set <key> <value>    # change one setting
release-check config unset <key>          # remove one setting
release-check config password             # set the password (prompted, not echoed)
```

A bare `config` prints each setting, its effective value and where that value
came from — `environment`, a file path, or `default`. That third column is the
answer to "why isn't my change taking effect".

`config set password` is refused: it would put the password in your shell
history and in `ps` output. Use `config password`.

### Settings

| Key | Environment variable | Default | Meaning |
|---|---|---|---|
| `url` | `NAVIDROME_URL` | — | Navidrome base URL. Required. A bare `host:port` is assumed to be http. A trailing `/rest` is stripped. |
| `username` | `NAVIDROME_USERNAME` | — | Navidrome username. Required. |
| `password` | `NAVIDROME_PASSWORD` | — | Navidrome password. Required. Never echoed. |
| `timeout` | `REQUEST_TIMEOUT_SECONDS` | `20` | Per-request timeout in seconds. |
| `cache-path` | `CACHE_PATH` | `~/.local/state/release_check/state.sqlite3` | Where local state lives. |
| `cache-max-age` | `CACHE_MAX_AGE_HOURS` | `24` | Lifetime for volatile cache entries. Album track lists are kept 30 days regardless. |
| `types` | `RELEASE_TYPES` | all | Release types to report, comma separated: `album`, `ep`, `single`, `unknown`. |

Precedence, highest first:

1. A real environment variable
2. `--env-file <path>`, or `$RELEASE_CHECK_ENV`
3. `./.env` in the current directory
4. `~/.config/release_check/.env`

A value set in your shell environment silently overrides the file. `config` will
tell you when that's happening.

### `check`

```bash
release-check check
```

Verifies connectivity and credentials, prints the server version and the number
of visible album artists, then exits. No Deezer traffic, no state written.

---

## Scanning

### `scan`

```bash
release-check                             # the whole library
release-check --since 2024-06-01          # only releases on or after this date
release-check --artist "Björk" --artist "Ghost"
release-check --limit 20 --type album
```

| Flag | Effect |
|---|---|
| `--artist NAME` | Restrict to this local artist. Repeatable. |
| `--limit N` | Scan at most N artists. |
| `--since DATE` | Only consider releases on or after `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. |
| `--type TYPE` | Only report `album`, `ep`, `single` or `unknown`. Repeatable. Overrides the `types` setting for this run. |
| `--refresh` | Ignore cached Deezer data and refetch everything. Slow. |
| `--flat` | One continuous list sorted by date, without release-type groups. |
| `--no-progress` | Suppress the progress line. |

Output is written to stdout in three parts: the results, a summary, and — on
stderr — the "needs review" and "unresolved artists" sections. Piping to a file
therefore captures the results and leaves the follow-up prompts on your
terminal.

Interrupting with Ctrl-C is safe. Everything already fetched stays cached, so a
re-run resumes cheaply.

---

## Correcting the results

Three different things can be wrong with a scan, and each has its own command.

### The artist is wrong — `resolve`, `map`, `unmap`, `block`

```bash
release-check resolve                     # walk everything the scan couldn't match
release-check resolve "Ghost"             # re-open one artist, even if already mapped
release-check map "Ghost" 1160651 4859761 # set it directly, no prompts
release-check unmap "Ghost"               # forget everything about this artist
release-check block --artist "Karaoke Hits Vol 3"   # never report this artist
release-check block --artist 1160651               # ...or name it by Deezer id
release-check unblock --artist "Karaoke Hits Vol 3" # undo
release-check artists                     # list what's unresolved
release-check artists --mappings          # list what's mapped or blocked
```

`resolve` is interactive. For each artist it shows up to six Deezer candidates
with a few of their album titles — titles you already own are listed first,
because that's what actually identifies the right artist. At the prompt:

| Key | Action |
|---|---|
| `1`, or `1 3`, or `1,3` | Map to these candidates. Several are merged and de-duplicated. |
| `d <id or URL>` | Map to a Deezer artist not in the list. |
| `s` or Enter | Skip, leave as-is. |
| `b` | Block this artist. |
| `c` | Clear what's stored, resolve from scratch next scan. |
| `q` | Quit. |

`block --artist` accepts three spellings of the same artist: the **local name**
as it appears in your Navidrome library, a **Deezer artist id**, or a **Deezer
artist URL**. An id already mapped resolves back to your local name; an unmapped
id is looked up on Deezer and its name used. The local name always works, which
matters because an artist with no Deezer counterpart at all — a karaoke
compilation, a mis-tagged folder — is exactly the kind you most want to block.

Name matching folds case, accents and punctuation, so `"bjork"` finds `Björk`.
Quote names containing spaces. `block "<artist>"` without a flag is shorthand
for `block --artist "<artist>"`.

`unblock --artist` lifts a block and nothing else: if the artist is mapped
rather than blocked it says so and leaves the mapping alone. `unmap` is the
bigger hammer — it clears mappings *and* blocks, returning the artist to
unresolved.

### One release is wrong — `block --album`, `review`

```bash
release-check block --album 558123              # don't report this, whatever it is
release-check block --album https://deezer.com/album/558123
release-check unblock --album 558123            # undo

release-check review                            # walk the review queue interactively
release-check review 558123 --own               # I have it; stop reporting it
release-check review 558123 --missing           # I don't have it; always report it
release-check review 558123 --clear             # forget the decision
```

Both take a **Deezer album ID or URL** — copy it straight from the results
list. `--album` covers every release type: albums, EPs and singles are all
albums to Deezer, and to this flag.

`block --album` and `review --own` both stop a release being reported, and they
are deliberately different: `--own` records a claim about your library, `block`
records only that you don't want to hear about it. `unblock --album` clears
either one.

A recorded decision beats the matcher outright and is never re-litigated.
Interactive review keys: `o` own, `m` missing, `s`/Enter skip, `u` undo, `q` quit.

### Which suppression to use

| | Scope | Identified by | Undo |
|---|---|---|---|
| `block --artist X` | every release by that artist, forever | local name, Deezer id, or Deezer URL | `unblock --artist X` |
| `block --album X` | one release | Deezer album id or URL | `unblock --album X` |
| `review X --own` | one release, as *owned* | Deezer album id or URL | `review X --clear` |

---

## State

One SQLite file holds everything the tool remembers. It contains two very
different kinds of thing, and the difference is the whole point of this section:

**Cached API responses** — Deezer search results, discographies, album detail
and track lists, plus your Navidrome album track lists. Disposable. Deleting
them costs a slow next scan and nothing else. Two expiry classes:

| Class | Contents | Lifetime |
|---|---|---|
| Volatile | artist searches, discography listings | `cache-max-age`, 24h by default |
| Stable | album detail and track lists | 30 days |

The split exists because a released album's track list never changes, while a
discography listing exists precisely to change. Expiring both on the same clock
re-fetched thousands of immutable records for nothing. Navidrome album tracks
are cached against a fingerprint of the album's song count and duration, so they
invalidate themselves when you actually change the album.

**Your decisions** — artist mappings, blocks, and review decisions. Not
disposable. These are the answers you typed, and nothing can recreate them.

### `cache`

```bash
release-check cache                    # what's stored: path, counts, cache age
release-check cache --clear            # delete cached API responses
release-check cache --reset-mappings   # delete all artist mappings
release-check cache --reset-decisions  # forget every review decision
```

`--clear` only touches the disposable half; the next scan just runs slowly.
Reach for it when Deezer data looks stale or wrong. `--refresh` on a scan does
the same thing for one run without deleting anything.

`--reset-mappings` and `--reset-decisions` destroy the other half. They are
immediate and unconfirmed, and there is no undo.

---

## What needs a Navidrome server

Only `scan` and `check` talk to Navidrome, and only they require credentials.
`resolve` uses them if present — the picker highlights candidates whose album
titles you already own — but works without.

Everything else reads and writes the local state file, or talks to Deezer, whose
public API needs no credentials at all: `artists`, `cache`, `block`, `unblock`,
`map`, `unmap`, `review`, and `config`.

---

## Global flags

Accepted before or after the subcommand.

| Flag | Effect |
|---|---|
| `-v`, `-vv` | Verbose to stderr; twice for debug logging. |
| `--env-file PATH` | Read and write settings at this path. |
| `--version` | Print the version and exit. |
| `-h`, `--help` | Help. Works per-subcommand: `release-check scan --help`. |

---

## Files

| Path | Contents |
|---|---|
| `~/.config/release_check/.env` | Settings, including the password. Mode 600. |
| `~/.local/state/release_check/state.sqlite3` | Cache, artist mappings, review decisions, last scan's queues. |
| `~/.local/share/release-check/` | The installed virtualenv. |
| `~/.local/bin/release-check` | The symlink on your `PATH`. |

Overrides for testing: `RELEASE_CHECK_CONFIG_DIR`, `RELEASE_CHECK_STATE_DIR`,
`RELEASE_CHECK_ENV`.

Deleting the state file loses your mappings and review decisions. Deleting the
`.env` loses your credentials. Nothing else is written anywhere.

---

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

Code 7 still prints results; the summary says so. One failing artist never stops
the rest of the scan.
