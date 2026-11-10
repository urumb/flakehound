# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub's
[private vulnerability reporting](https://github.com/urumb/flakehound/security/advisories/new).
You should get a response within 7 days. Please include steps to reproduce and the affected version.

## Supported versions

Only the latest release receives security fixes while the project is pre-1.0.

## Security model

- **flakehound runs your test suite, which is arbitrary code.** Only run it on code you trust.
  Never point it at untrusted pull requests or repositories without a sandbox.
- **Offline by default.** flakehound makes no network calls and collects no telemetry.
  Features that need the network (such as future GitHub history mode or cloud AI) are opt-in.
- **No secrets in output.** Reports never include environment variables; values that look like
  tokens, keys or passwords are redacted.
- **Safe subprocesses.** Tests run via argument lists, never `shell=True`. Child processes and
  temporary files are cleaned up, even on Ctrl+C.
- **Supply chain.** Minimal dependencies, releases published with PyPI Trusted Publishing
  (no long-lived tokens), GitHub Actions pinned to commit SHAs with least-privilege permissions.
