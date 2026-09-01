# ghostpkg

**Catch package names that don't exist before you install them.**

[![CI](https://github.com/m1rwana12/ghostpkg/actions/workflows/ci.yml/badge.svg)](https://github.com/m1rwana12/ghostpkg/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ghostpkg.svg)](https://pypi.org/project/ghostpkg/)
[![Python](https://img.shields.io/pypi/pyversions/ghostpkg.svg)](https://pypi.org/project/ghostpkg/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Dependencies: 0](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](pyproject.toml)

Language models invent package names. A study of ~200,000 code-generation
prompts found **205,474 unique hallucinated package names**, and **43% of those
hallucinations reappeared in all ten reruns of the same prompt** — which makes
them predictable enough for an attacker to register in advance. That attack has
a name now: *slopsquatting*.

`ghostpkg` checks names against the real registry before anything gets
installed.

```console
$ ghostpkg check requests-async-helper-sdk numpy

  BLOCKED  requests-async-helper-sdk
           - does not exist on pypi
  ok       numpy  (136 releases, 19.8y old)

  1 blocked: requests-async-helper-sdk
```

Exit code is `1` when something is blocked, so it drops straight into CI or a
pre-install hook.

## Install

```bash
pip install ghostpkg      # or: uvx ghostpkg, pipx install ghostpkg
```

Zero runtime dependencies, standard library only. A supply-chain security tool
that drags in a dependency tree isn't much of a security tool.

## Use

```bash
# check names directly
ghostpkg check fastapi-middleware pandas-utils

# check an npm name
ghostpkg check react-router-dom-utils -e npm

# check every dependency in a manifest
ghostpkg scan requirements.txt
ghostpkg scan package.json

# machine-readable
ghostpkg check somepkg --json
```

## What it actually checks

| Signal | Meaning |
|---|---|
| Not in the registry | **Blocked.** The name is a ghost — nothing to install. |
| Published days ago | Warning. Attackers register fast — but so do honest authors. |
| One release only | Warning. |
| No repository or homepage | Warning. |
| One or two characters from a popular name, *and* recently published | Warning. Possible typosquat. |

## Why it doesn't block on "suspicious", and why that matters

The obvious design is to score packages and block anything that looks shady.
I built that first and measured it against the live PyPI feed. It flagged
**100% of legitimate packages published that day.**

That result is not a tuning problem, it's the shape of the data: a malicious
slopsquat registered three days ago and an honest new library published three
days ago are *the same package* from the outside. Both are young, both have one
release, both often lack a repository link.

So `ghostpkg` blocks on exactly one signal — **the package does not exist** —
because that one is precise, and it's the one that actually corresponds to a
hallucination. Everything softer is a warning for a human to read.

```console
$ ghostpkg check react-router-dom-utils -e npm

  WARNING  react-router-dom-utils
           - first published 176 days ago
           - only one release
           - no repository or homepage link
```

If you want the aggressive behaviour, `--strict` promotes warnings to blocks.
It is not the default, and it will flag real packages.

## Why not X?

| | ghostpkg | Registry-scanning SCA (Snyk, Socket) | `pip install` alone |
|---|---|---|---|
| Catches a name that doesn't exist | **yes, before install** | after install / in a PR | no |
| Runs offline of any account | **yes** | account required | — |
| Runtime dependencies | **0** | many | — |
| Blocks legitimate new packages | **no** | varies | — |
| npm + PyPI in one tool | **yes** | yes | no |

`ghostpkg` is deliberately narrow. It does not scan code, detect malware, or
replace an SCA product. It answers one question well.

## Honest limitations

- **The hard case is out of scope today.** A hallucinated name that an attacker
  has *already registered* will pass the existence check. The warning signals
  are what stand between you and that, and they are advisory. Improving this is
  the main open problem — see [issues](https://github.com/m1rwana12/ghostpkg/issues).
- Typo detection compares against the 2,000 most-downloaded PyPI projects, so a
  squat on a less popular package won't be flagged as a lookalike.
- npm scoped packages (`@scope/name`) are checked, but the popular-name list is
  PyPI-derived, so npm typosquat detection is weaker.
- Every check is a live registry request. No caching yet.

## Prior art and credit

The scale of the problem was established by Spracklen et al., *"We Have a
Package for You! A Comprehensive Analysis of Package Hallucinations by Code
Generating LLMs"* (USENIX Security 2025).

Those authors deliberately **did not publish** their list of hallucinated
package names, because such a list is a ready-made target list for attackers.
`ghostpkg` follows that decision and ships no corpus of hallucinated names —
it checks names live instead.

## Contributing

Issues and PRs welcome. `pip install -e ".[dev]" && pytest`.

## License

MIT
