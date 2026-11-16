# Known issues — 0.1.0-alpha.1

- Clean Debian13 apt installation, upgrade/removal and real conversational E2E on a
  second machine are not confirmed. Build/extracted-payload checks are distinct.
- Copyright holder in NOTICE is intentionally pending owner input; fill before release.
- CPU responses can take minutes. Context8192 is finite; long/ambiguous conversations
  and references can fail or require clarification. Other GGUF models may not obey
  tool schemas or complete goals. No general autonomous-success claim is made.
- Independent goal verification is only available for supported formal conditions.
  Informational replies and arbitrary goals may remain UNVERIFIED.
- Shell effects outside controlled publication are not transactional. Resume may
  require reconciliation/UNCERTAIN; never blindly replay unknown operations.
- Sandboxing needs user namespaces, LandlockABI>=3, seccomp and user systemd. Locked
  down hosts may not support them; no unsafe fallback is provided.
- Host observations expose a limited read-only surface, not general root authority.
  Wi-Fi observations are NetworkManager cache, not an authorized active rescan;
  absence of nmcli/hardware limits availability. No network configuration is changed.
- External backend identity/resource hard limits are operator responsibilities.
  Managed backend estimates memory and stops on failure; no automatic emergency
  model substitution is shipped. New-host service lifecycle remains unqualified.
- Pause/close clients before upgrades; hot code replacement is not supported.
- After-logout user-manager behavior depends on host policy; linger is never enabled
  automatically. No new SSH persistence/model-memory campaign was performed.
- No formal security audit or authenticated release-signing infrastructure yet.
