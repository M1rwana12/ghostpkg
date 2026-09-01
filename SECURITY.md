# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/M1rwana12/ghostpkg/security/advisories/new)
rather than a public issue.

## Threat model

`ghostpkg` is a *advisory* gate, not a sandbox. It is worth being explicit about
what it does and does not protect against.

### What it catches

- A package name that does not exist in the registry. This is the shape of an LLM
  hallucination, and it is the only signal the default profile blocks on.
- Weak signals, reported as warnings for a human: very recent publication, a
  single release, no repository link, and small edit distance to a popular name.

### What it does not catch

- **A hallucinated name an attacker has already registered, when `--deep` is
  off.** The existence check passes and only advisory warnings remain. With
  `--deep`, install-time code is statically inspected and the usual malicious
  shapes are caught, which measured 0 false positives across 27 established and
  32 same-day packages while catching all 6 test shapes.
- **Even with `--deep`:** a squat whose payload runs at *import* time rather than
  install time, obfuscation beyond the documented patterns, and any package
  published without an sdist, which cannot be inspected.
- Malicious code in a package that is otherwise legitimate and established.
- Compromise of an existing maintainer account.
- Malicious behaviour at *runtime*. `--deep` reads install-time code only.

### Failure mode

If a registry is unreachable, `ghostpkg` exits with code `2` rather than passing
silently. A network failure will fail your build. That is deliberate: a security
check that quietly succeeds when it could not run is worse than no check.

### How `--deep` handles untrusted archives

- Archives are read **in memory**, never extracted to disk, so a path-traversal
  entry has nothing to write to.
- **Nothing is executed, imported or compiled.** Only named install-time files
  are read, and only as text.
- Downloads stop at 8 MB and individual members at 512 KB, so a decompression
  bomb cannot exhaust memory.
- Any failure to download or parse means "not inspected", never a pass.

### Trust boundaries

- Requests go only to `pypi.org`, `files.pythonhosted.org` and
  `registry.npmjs.org` over HTTPS.
- No telemetry, no analytics, no phoning home.
- No runtime dependencies, so the tool's own supply chain is the Python standard
  library.

## Supported versions

The latest release is supported. This project is pre-1.0 and the policy will be
revisited at 1.0.
