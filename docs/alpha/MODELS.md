# Models and backend

Use a trustworthy GGUF compatible with your llama.cpp version. Recommended for
suitable hardware: Qwen3.5 9B Q4_K_M, non-thinking, context8192/output2048. Other
GGUFs can be selected in setup; smaller machines should start with a smaller model
and context4096/output1024. No claim that arbitrary model obeys the agent protocol.

Download is a separate explicit user action requiring Internet unless you already
have the file. Verify publisher license, checksum and provenance. Do not rename
another file to GGUF. Header check is only format triage; successful server load
and doctor --probe are still necessary. Keep weights outside /usr/lib/neuroos.

Install/build CPU llama.cpp separately from https://github.com/ggml-org/llama.cpp .
Use a CPU build compatible with your machine, not an untrusted developer-native
binary. Official server API documentation:
https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md .
This package does not redistribute any inference binary/library or GGUF.

External standalone example (run by the user, replace paths and size as appropriate):
`llama-server --model /path/model.gguf --alias neuroos-local --host 127.0.0.1 --port 8081 --ctx-size 4096 --parallel 1 --slots --n-gpu-layers 0`
For this external mode supply your own resource-limited supervisor. Prefer setup's
managed mode if you need a bounded user service without privileged installation.
Do not point setup at someone else's production server expecting automatic switching.

Managed service always starts CPU-only, one slot, chosen context/alias, small batch
settings and explicit memory cap. A denied estimate is not proof a model cannot
run, but alpha does not force a load, reclaim global cache or weaken limits.
Choose a smaller model/context; measure your own actual peak before advanced tuning.
