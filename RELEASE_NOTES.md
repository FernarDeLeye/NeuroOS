# NeuroOS 0.1.0-alpha.2

NeuroOS is an offline conversational Linux agent that uses a user-selected local
model and Linux tools. Alpha 2 focuses on reducing repeated context processing
without discarding the durable conversation or object evidence.

## Context efficiency

In one sequential experiment on a Lenovo X230 with Qwen3.5 9B Q4_K_M, context
8192 and non-thinking inference:

- Warm median prefill time decreased from 216.14 s to 131.57 s (-39.1%).
- Warm median full-response latency decreased from 232.34 s to 148.14 s (-36.2%).
- Median newly evaluated input tokens decreased from 968.5 to 554.5.
- Weighted warm input-cache reuse increased from 61.5% to 80.6%.
- Seven conversational goals met the same accepted criteria in baseline and optimized
  runs; both file-task runs independently reached VERIFIED after one repair.

The complete seven-turn conversation was 24.6% faster, not 36.2%; first cold-KV
requests remained about 4.6 minutes. This was a single sequential host experiment,
not a randomized multi-machine study. Results are not guaranteed on other CPUs,
models, llama.cpp builds, prompts or workloads.

Object binding, exact source observations and full history remain stored. Eligible
delivered observations are referenced compactly; uncertain, changed, failed and
current-task observations are not projected away.

## Compatibility and limits

Debian 13 amd64, CPU-only. No model weights or inference binaries are included. The
installer, model configuration, rootless execution, publication, verification and
repair contract are carried forward from Alpha 1. Read README.md, INSTALL.md and
KNOWN_ISSUES.md before use. This alpha is not intended for critical administration.
