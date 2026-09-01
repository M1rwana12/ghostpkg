# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/M1rwana12/ghostpkg/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.5.0
[0.4.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.4.0
[0.3.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.3.0
[0.2.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.2.0
[0.1.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.1.0
