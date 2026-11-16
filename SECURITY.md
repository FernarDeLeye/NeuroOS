# Security policy — experimental alpha

Do not use this alpha for critical systems, secrets handling, destructive host
administration or unattended production work. Local execution is not inherently
safe: model output and untrusted files can mislead an agent. Rootless isolation,
permissions and postconditions reduce risk; they are not a formal security proof.

No unrestricted root, automatic model downloads or automatic telemetry uploads.
Read-only host observations may still contain private information. Do not post raw
chats, commands, DB, network identities or content traces. Feedback is metadata-only
and must be reviewed locally before voluntary sharing.

Before opening the repository, maintainers must enable GitHub private vulnerability
reporting and confirm its availability. Use the repository Security tab's private
report mechanism for vulnerabilities; never publish credentials or exploit details
in an issue. If private reporting is unavailable, ask for a private reporting route
without revealing the vulnerability. No email address has been invented here.

Only the current alpha is an active testing target; no support SLA or supported
production release exists. Maintainers should reproduce with synthetic data,
coordinate disclosure and record fixed versions. See KNOWN_ISSUES.md for constraints.
