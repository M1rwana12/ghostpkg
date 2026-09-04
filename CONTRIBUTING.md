# Contributing to ghostpkg

Thanks for looking. This is a small, deliberately narrow tool, and the bar for
changes is mostly about keeping it that way.

## Getting set up

```bash
git clone https://github.com/M1rwana12/ghostpkg
cd ghostpkg
pip install -e ".[dev]"
pytest
```

The test suite constructs `PackageFacts` directly and never touches the network,
so it runs offline and cannot break because a real package changed.

## The two rules that shape this project

**1. No runtime dependencies.** A supply-chain security tool that installs a
dependency tree of its own undermines its own argument. Standard library only.
A pull request that adds a runtime dependency will be declined regardless of how
useful the dependency is.

**2. A false positive is worse than a miss.** People run this in front of every
install. If it cries wolf on a legitimate package, they turn it off, and then it
protects nothing.

That second rule is not a preference, it's a measurement. The first version of
this tool scored packages on age, release count and missing repository links,
then blocked anything suspicious. Tested against the live feed of newly published
PyPI packages, it flagged **100% of them**. A malicious slopsquat registered
three days ago and an honest new library published three days ago are the same
package from the outside.

So: the default profile blocks on non-existence only. If you want to propose a
new blocking signal, bring evidence that it does not fire on legitimate packages.

## Especially welcome

- **False positive reports.** If `ghostpkg` flagged something real, that's a bug
  and it's the most valuable report you can file.
- **The open problem:** detecting a hallucinated name that an attacker has
  *already registered*. The existence check cannot see it. If you have an idea
  that does not collapse into "block everything new", open an issue.
- Wider popular-name lists. There are 2,000 names per ecosystem now and each
  name is compared against its own registry's list, so `recat`, `lodahs` and
  `webpakc` are caught -- but a squat on anything outside the top 2,000 is not.
- More ecosystems: crates.io, RubyGems, Go modules.

## Please don't

- **Do not add a corpus of hallucinated package names to this repository.** The
  authors of the USENIX'25 study withheld theirs deliberately, because such a
  list is a ready-made target list for attackers pre-registering those names.
  That reasoning applies here. This project checks names live and ships no corpus.
- Don't broaden the scope into code scanning or malware detection. Other tools do
  that well.

## Style

Match the surrounding code. Type hints on public functions, `from __future__
import annotations` at the top of each module (the package supports Python 3.9).
Comments explain *why*, not *what* — several comments in `assess.py` record the
measurement behind a decision, and that is the kind worth writing.

## Pull requests

Keep them focused. Add a test. CI runs on Linux, macOS and Windows across Python
3.9, 3.12 and 3.13, and it needs to be green.
