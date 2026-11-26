# Known issues — 0.1.0-alpha.2

- Clean Debian 13 apt installation, Alpha 1 to Alpha 2 upgrade, removal and real
  conversational E2E on a second machine have not been confirmed for this build.
- CPU responses can still take minutes. The first request after a cold KV cache was
  about 4.6 minutes in the X230 experiment. The reported improvement describes one
  sequential run and may not generalize.
- Context is finite. Long or ambiguous conversations can need clarification; other
  GGUF models may not follow tool schemas or complete goals.
- Independent verification is available only for supported formal conditions.
  Informational replies and arbitrary goals can remain UNVERIFIED.
- Shell effects outside controlled publication are not transactional. Resume may
  require reconciliation/UNCERTAIN; operations with unknown outcomes are not replayed
  automatically.
- Sandboxing requires user namespaces, Landlock ABI >=3, seccomp and a user systemd
  manager. Locked-down hosts receive no insecure fallback.
- Host observations provide a limited read-only surface, not general root access.
  Wi-Fi observations use NetworkManager's cached scan results; no active scan or
  network configuration change is performed.
- The compatible local backend and user-selected GGUF are external prerequisites.
  Model memory needs vary; 9B Q4_K_M is not a universal minimum profile.
- User-manager behavior after logout depends on host policy. Linger is not enabled
  automatically. Pause clients before upgrades; hot code replacement is unsupported.
- No formal security audit, signed release metadata or clean-VM package lifecycle
  qualification has been completed. Do not use for critical system administration.
