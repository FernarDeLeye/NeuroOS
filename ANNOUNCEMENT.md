# Draft announcement — publish only after final approval

**NeuroOS 0.1.0-alpha.2: an offline conversational Linux agent**

NeuroOS is a local text interface where a user describes a Linux goal, a local LLM
selects tools, and the runtime checks what actually happened. It is an application,
not a new distribution or cloud service. The alpha targets Debian 13 amd64 with a
separately installed local llama.cpp backend and user-provided GGUF.

Alpha 2 reduces repeated conversation context processing while retaining durable
history and structured references. In one sequential X230 run with Qwen3.5 9B
Q4_K_M, warm median prefill fell 39.1%, warm median full-response latency fell
36.2%, and newly evaluated input tokens fell from 968.5 to 554.5. The whole seven-
turn conversation improved 24.6%; cold first requests still took about 4.6 minutes.
These are single-host, workload-specific measurements, not guarantees.

This is an early alpha, not suitable for critical administration. Model behavior
varies; independent verification only covers supported conditions; no model weights
or backend binary are bundled. There is no automatic telemetry or model download.
Please read README, INSTALL, KNOWN_ISSUES and SECURITY before testing. Synthetic
reproductions and reviewed metadata-only feedback are welcome.

Repository/release link: https://github.com/FernarDeLeye/NeuroOS/releases/tag/v0.1.0-alpha.2
