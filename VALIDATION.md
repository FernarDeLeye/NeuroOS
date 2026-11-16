# Validation status — 0.1.0-alpha.1

This standalone source is derived from the portable application payload, not the
private experimental Git history. No private database or trace is an input fixture.
Apache-2.0 was selected by the owner. NOTICE attribution is intentionally pending.

## Checks performed during preparation

See the accompanying artifact VALIDATION_REPORT.json / audit report for actual
results. Standard Debian build tests, file/dependency/maintainer-script checks,
CLI and isolated execution checks must be distinguished from apt installation.
Existing development evidence used an actual local model; it does not qualify a
new machine/model or this snapshot's clean-system installation.

## Not confirmed on a clean second system

Clean Debian13 apt install/dependency resolution, model load, real natural-language
OS question/file action, conversation persistence/pause/resume, upgrade and removal.
A simulated apt run or extracted directory is NOT a real installation. No VM/QEMU/
KVM is available in the preparation environment; rootless bootstrap lacks newuidmap.
No privileged host changes or model campaign are authorized/performed to hide that
limitation. Lintian availability is recorded in the report, not assumed.

Use packaging/clean-vm-test.sh only in a disposable Debian13 VM, read it first and
set NEUROOS_CLEAN_VM=YES. Supply a separate genuine llama.cpp/GGUF; no mock inference.
Record all checks and failures, distinguish model from infrastructure failures.
Its metadata-only package upgrade tests dpkg lifecycle, not a future code migration.
Never install over a live experimental deployment.

## Reproduction

```
python3 -m unittest discover -s packaging/tests -v
python3 packaging/audit_public.py . --output /tmp/neuroos-audit.json
```

Source-tree audit is heuristic and includes reachable Git blobs, not every possible
secret format. Review manifests manually. No claim of production readiness or
independent professional security review is made.
