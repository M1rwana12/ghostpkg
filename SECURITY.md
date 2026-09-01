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

- **A hallucinated name an attacker has already registered.** The existence check
  passes. Only the advisory warnings stand between the user and it. This is the
  main open problem and it is stated plainly in the README rather than hidden.
- Malicious code in a package that is otherwise legitimate and established.
- Compromise of an existing maintainer account.
- Anything at install time. `ghostpkg` inspects registry metadata; it never
  downloads, unpacks or executes a package.

### Failure mode

If a registry is unreachable, `ghostpkg` exits with code `2` rather than passing
silently. A network failure will fail your build. That is deliberate: a security
check that quietly succeeds when it could not run is worse than no check.

### Trust boundaries

- Requests go only to `pypi.org` and `registry.npmjs.org` over HTTPS.
- No telemetry, no analytics, no phoning home.
- No runtime dependencies, so the tool's own supply chain is the Python standard
  library.

## Supported versions

The latest release is supported. This project is pre-1.0 and the policy will be
revisited at 1.0.
