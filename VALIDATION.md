# Validation status — 0.1.0-alpha.2

Alpha 2 derives from the existing public Alpha 1 repository history and overlays
only the context runtime delta from development commit `bc860ed951f49828d501c7d6f4602cee162f4766`, plus versioned docs and focused regression tests. Structured object binding was already present in Alpha 1; its binding modules are unchanged. The production model adapter and installer remain the audited Alpha 1 versions.

See `VALIDATION_REPORT.json` distributed alongside release artifacts for actual build,
test, payload and audit outcomes. Build artifacts are tested independently from apt
installation and model quality.

## Clean system status

No clean Debian 13 VM or equivalent VM runtime was available during preparation.
Therefore apt installation, Alpha 1 to Alpha 2 upgrade, chat after install and package
removal on a clean system are **not confirmed**. Extracted-payload checks and Debian
metadata checks do not substitute for that lifecycle test. No test package was
installed over the live X230 development deployment.

A guarded `packaging/clean-vm-test.sh` is included for a disposable Debian 13 VM. Read
it first, use a separately obtained real backend/model, and distinguish package
failures from model behavior. Do not run it on the development host.
