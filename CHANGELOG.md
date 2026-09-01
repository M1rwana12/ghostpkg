# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/M1rwana12/ghostpkg/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/M1rwana12/ghostpkg/releases/tag/v0.1.0
