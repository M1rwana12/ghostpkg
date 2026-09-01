# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/M1rwana12/ghostpkg/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.2.0
[0.1.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.1.0
