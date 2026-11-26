# Requirements — 0.1.0-alpha.2

Supported packaging target: Debian 13 amd64, CPU-only, Python >=3.11 and an ordinary
user systemd manager. Rootless user/mount/PID/network namespaces, Landlock ABI >=3
and libseccomp2 are required; there is no unsafe fallback. tmux is optional.
NetworkManager is only needed for corresponding read-only observations.

There is no fixed RAM floor independent of GGUF and context. The model, KV cache,
working buffers, workers and Linux reserve must fit. Small quantized models need
several GiB; Qwen3.5 9B Q4_K_M is an optional profile for suitable systems and was
measured on a single X230, not broadly qualified. No GGUF/backend binary is bundled.

The Alpha 2 context measurements are one sequential run; see RELEASE_NOTES.md.
Performance varies with model, context, cache state, CPU and llama.cpp version.
