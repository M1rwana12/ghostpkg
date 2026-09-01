<div align="center">

<img src="https://raw.githubusercontent.com/M1rwana12/ghostpkg/main/assets/banner.png" alt="ghostpkg" width="100%">

<h1>ghostpkg</h1>

**Stops you installing packages that don't exist.**

Language models invent library names. Attackers register those names in advance.
`ghostpkg` checks the name against the real registry — before `pip` or `npm` downloads anything.

**English** · [Українська](https://github.com/M1rwana12/ghostpkg/blob/main/README.md)

[![CI](https://github.com/M1rwana12/ghostpkg/actions/workflows/ci.yml/badge.svg)](https://github.com/M1rwana12/ghostpkg/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ghostpkg?color=A78BFA&label=pypi)](https://pypi.org/project/ghostpkg/)
[![Python](https://img.shields.io/pypi/pyversions/ghostpkg?color=A78BFA&label=python)](https://pypi.org/project/ghostpkg/)
[![Dependencies](https://img.shields.io/badge/dependencies-0-3FB950.svg)](https://github.com/M1rwana12/ghostpkg/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-8B949E.svg)](https://github.com/M1rwana12/ghostpkg/blob/main/LICENSE)

</div>

---

## Contents

- [The problem](#the-problem)
- [What ghostpkg does](#what-ghostpkg-does)
- [Install](#install)
- [Usage](#usage)
- [Every signal it checks](#every-signal-it-checks)
- [Why it doesn't block on "suspicious"](#why-it-doesnt-block-on-suspicious)
- [Where it fits in your workflow](#where-it-fits-in-your-workflow)
- [How it works internally](#how-it-works-internally)
- [Comparison](#comparison)
- [Honest limitations](#honest-limitations)
- [Prior art and credit](#prior-art-and-credit)

---

## The problem

A study of ~200,000 code-generation prompts found **205,474 unique hallucinated
package names**. The dangerous part isn't the count — it's the consistency:

> **43% of those hallucinations reappeared in all ten reruns of the same prompt.**

That makes them **predictable**. An attacker can work out in advance which name a
model will invent, register it on PyPI or npm, and wait. By the time your agent
confidently writes `pip install fastapi-middleware`, the package **is already there**.

The attack has a name: **slopsquatting**.

This isn't theoretical. Malicious packages exploiting exactly this pattern have
been found with tens of thousands of downloads, and the Cloud Security Alliance
published a formal research note on it in April 2026.

---

## What ghostpkg does

It answers one question, precisely: **does this package actually exist, and if so,
does anything about it look wrong?**

<div align="center">
  <img src="https://raw.githubusercontent.com/M1rwana12/ghostpkg/main/assets/demo.gif" alt="ghostpkg blocking a package that does not exist" width="100%">
</div>

The exit code is `1` when anything is blocked, so it drops straight into CI or a
pre-install hook with no glue code.

**What it is:** a fast, narrow, dependency-free gate you can put in front of every
install.

**What it is not:** a code scanner, a malware detector, or a replacement for an SCA
product. It does one job.

---

## Install

```bash
pip install ghostpkg
```

Other ways:

```bash
uvx ghostpkg check requests      # run without installing
pipx install ghostpkg            # isolated, global command
```

> [!NOTE]
> **Zero dependencies.** Python standard library only.
> A supply-chain security tool that drags in a dependency tree of its own is a
> questionable proposition. This one has none.

Requires Python 3.9+. Tested on Linux, macOS and Windows.

---

## Usage

### Check names directly

```bash
ghostpkg check fastapi-middleware pandas-utils
ghostpkg check react-router-dom-utils -e npm
```

### Scan a manifest

```bash
ghostpkg scan requirements.txt
ghostpkg scan pyproject.toml
ghostpkg scan package.json
```

| Manifest | What is read |
|---|---|
| `requirements*.txt` | Every requirement line. Comments, flags, VCS URLs and direct links are skipped. |
| `pyproject.toml` | PEP 621 `project.dependencies` and `project.optional-dependencies`, plus Poetry's `tool.poetry` groups. `[build-system] requires` is not included, and the `python` constraint is not treated as a package. |
| `package.json` | `dependencies`, `devDependencies`, `optionalDependencies`. |

Anything else is **refused with an error rather than guessed at**. Guessing is
what made an earlier version read `pyproject.toml` with the requirements parser
and report TOML keys as package names.

### Options

| Flag | Purpose |
|---|---|
| `-e`, `--ecosystem` | `pypi` (default) or `npm` |
| `--strict` | Promote warnings to blocks |
| `--json` | Machine-readable output for scripts and CI |
| `-q`, `--quiet` | Hide packages that passed |
| `--version` | Print the version |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Nothing blocked (warnings may still be present) |
| `1` | At least one package blocked |
| `2` | Usage error, unreadable manifest, or the registry was unreachable |

### JSON output

```console
$ ghostpkg check somepkgthatisnotreal9911 --json
[
  {
    "name": "somepkgthatisnotreal9911",
    "ecosystem": "pypi",
    "verdict": "BLOCK",
    "reasons": [
      "does not exist on pypi"
    ]
  }
]
```

---

## Every signal it checks

| Signal | Verdict | Why |
|---|---|---|
| Not in the registry | 🔴 **Blocked** | The name is a ghost. There is nothing to install, and this is exactly what a hallucination looks like. |
| First published < 90 days ago | 🟡 Warning | Attackers register fast. So do honest authors — hence a warning, not a block. |
| First published < 1 year ago | 🟡 Warning | Weaker version of the same signal. |
| Only one release | 🟡 Warning | Squats are usually published once and abandoned. |
| No repository or homepage link | 🟡 Warning | Real projects almost always link to source. |
| 1–2 edits from a popular name, **and** recently published | 🟡 Warning | Classic typosquat shape. A swap of adjacent characters counts as one edit, because `recat`/`react` is what squatters actually publish. Age matters: an old lookalike is just a package with a similar name. |

Warnings are advisory by default. Nothing but non-existence blocks unless you pass
`--strict`.

---

## Why it doesn't block on "suspicious"

The obvious design is to score packages and block anything that looks shady. I built
that version first, then measured it against the **live feed of newly published PyPI
packages**.

> It flagged **100% of legitimate packages published that day.**

That is not a threshold-tuning problem — it's the shape of the data. A malicious
slopsquat registered three days ago and an honest new library published three days
ago are **the same package from the outside**. Both are young. Both have one release.
Both often lack a repository link.

So `ghostpkg` blocks on exactly one signal — **the package does not exist** — because
that signal is precise, and it's the one that actually corresponds to a hallucination.
Everything softer is reported for a human to read.

```console
$ ghostpkg check react-router-dom-utils -e npm

  WARNING  react-router-dom-utils
           - first published 176 days ago
           - only one release
           - no repository or homepage link
```

There is a second lesson baked into the code. A naive typosquat check using a flat
edit-distance budget flagged `flask`, `click` and `black` as typos **of each other** —
short popular names sit inherently close together. The budget now scales with name
length, and only applies to packages young enough to plausibly be a squat.

---

## Where it fits in your workflow

### In CI

```yaml
- name: Check dependencies exist
  run: |
    pip install ghostpkg
    ghostpkg scan requirements.txt
```

### As a pre-commit hook

```yaml
repos:
  - repo: local
    hooks:
      - id: ghostpkg
        name: ghostpkg
        entry: ghostpkg scan requirements.txt
        language: system
        files: requirements\.txt$
        pass_filenames: false
```

### In front of a coding agent

Point your agent's shell hook at `ghostpkg check` before it is allowed to run an
install command. The non-zero exit code stops the install, and `--json` gives the
agent a structured reason it can act on.

---

## How it works internally

```
  name ──▶ registry lookup ──▶ 404? ──yes──▶ BLOCK ("does not exist")
                                │
                                no
                                ▼
                      collect facts:
                      age · release count · repo link
                                │
                                ▼
                   young enough to be a squat?
                                │
                        yes ────┴──── no ──▶ OK
                         │
                         ▼
              typo distance to top 2,000 names
              (budget scales with name length)
                         │
                         ▼
                 reasons found? ──no──▶ OK
                         │yes
                         ▼
                  WARN  (BLOCK if --strict)
```

| Module | Responsibility |
|---|---|
| `registries.py` | PyPI and npm clients over `urllib`. Returns a `PackageFacts` record. |
| `assess.py` | The policy. Turns facts into a verdict plus human-readable reasons. |
| `data.py` | The 2,000 most-downloaded PyPI names, used only for typo distance. |
| `cli.py` | Subcommands, manifest parsing, colour output, exit codes. |

Lookups run concurrently across a small thread pool, so scanning a manifest costs
roughly one round trip rather than one per dependency.

---

## Comparison

| | `ghostpkg` | SCA scanners (Snyk, Socket) | `pip install` alone |
|---|:---:|:---:|:---:|
| Catches a name that doesn't exist | ✅ **before install** | after install / in a PR | ❌ |
| Runs without an account | ✅ | ❌ | — |
| Runtime dependencies | **0** | many | — |
| Blocks legitimate new packages | ❌ **no** | varies | — |
| PyPI + npm in one tool | ✅ | ✅ | ❌ |
| Detects known malware | ❌ | ✅ | ❌ |
| Licence / CVE analysis | ❌ | ✅ | ❌ |

`ghostpkg` is deliberately narrow, and the last two rows are where a real SCA product
earns its keep. Use both.

---

## Honest limitations

> [!WARNING]
> **The hard case is out of scope today.** A hallucinated name that an attacker has
> **already registered** will pass the existence check. The warning signals are all
> that stand between you and it, and they are advisory. Improving this is the main
> open problem — see [issues](https://github.com/M1rwana12/ghostpkg/issues).

- Typo detection compares against the 2,000 most-downloaded projects in each
  ecosystem, so a squat on a less popular package won't be flagged as a lookalike.
- Names shorter than five characters are not compared at all: below that the name
  space is too dense for edit distance to mean anything.
- Every check is a live registry request. There is no caching yet.
- Registry outages surface as exit code `2` rather than a silent pass — deliberately,
  but it does mean a flaky network fails your build.

---

## Prior art and credit

The scale of the problem was established by Spracklen et al., *"We Have a Package for
You! A Comprehensive Analysis of Package Hallucinations by Code Generating LLMs"*
([USENIX Security 2025](https://www.usenix.org/conference/usenixsecurity25)).

Those authors **deliberately did not publish** their list of hallucinated package
names, because such a list is a ready-made target list for attackers. `ghostpkg`
follows that decision and **ships no corpus of hallucinated names** — it checks names
live instead.

---

## Contributing

Issues and pull requests welcome — see [CONTRIBUTING.md](https://github.com/M1rwana12/ghostpkg/blob/main/CONTRIBUTING.md).

```bash
git clone https://github.com/M1rwana12/ghostpkg
cd ghostpkg
pip install -e ".[dev]"
pytest
```

**The most valuable contribution is a false positive report.** If `ghostpkg` flagged
a real package, that's a bug — a tool that cries wolf on legitimate packages gets
turned off, and then it protects nothing.

The threat model, and an explicit list of what this tool does **not** catch, is in
[SECURITY.md](https://github.com/M1rwana12/ghostpkg/blob/main/SECURITY.md).

## License

[MIT](https://github.com/M1rwana12/ghostpkg/blob/main/LICENSE)
