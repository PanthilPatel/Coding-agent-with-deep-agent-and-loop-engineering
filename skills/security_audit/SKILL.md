---
name: security_audit
description: Inspect and sanitize code for security vulnerabilities, unsafe functions, and credential leaks.
triggers:
  - security
  - audit
  - vulnerability
  - sanitize
---

# Security Audit Playbook

When performing a security audit on Python code:

1. **Static Analysis & Inspection:**
   - Scan for hardcoded secrets, tokens, passwords, or API keys.
   - Look for unsafe execution patterns like `eval()`, `exec()`, or unsanitized shell executions via `subprocess` without `shell=False`.
   - Identify weak cryptography or unsafe random number generators (e.g., `random` instead of `secrets` for security tokens).

2. **Remediation Strategy:**
   - Replace hardcoded secrets with environment variable loaders (`os.getenv`).
   - Wrap unsafe type conversions or file opens with path normalization and validation.
   - Use parameterized queries or secure APIs instead of raw string concatenation.

3. **Verification:**
   - Run tests to ensure security fixes do not introduce functional regressions.