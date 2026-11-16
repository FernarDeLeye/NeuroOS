# NeuroOS Next — 0.1.0-alpha.1

An **offline conversational Linux agent**: describe a goal in ordinary language,
let a local model select tools, and inspect what actually happened. NeuroOS is an
application running on Linux, not a new kernel, distribution, or hosted AI service.

This is a limited early alpha for independent testing and contributions. It is
**not suitable for critical administration or unattended production changes**.
The clean Debian installation checklist is not yet completed; read
[KNOWN_ISSUES.md](KNOWN_ISSUES.md) and [VALIDATION.md](VALIDATION.md).

## What it does

- Persistent terminal conversations, plain mode and an optional tmux layout.
- Model-selected read-only Linux observations and isolated shell/CLI execution.
- Durable tasks, process supervision, bounded repair and safe resume/reconciliation.
- Controlled file publication, collision checks and independent postconditions.
- Structured observations and object references across conversational turns.
- Local llama.cpp backend, user-selected GGUF; no cloud inference dependency.

## Architecture

`Terminal/tmux → conversation & presentation → model adapter → tools/observations`

Execution uses a rootless workspace (namespaces, Landlock, seccomp and resource
limits). SQLite stores conversations/tasks; a controlled publisher applies only
authorized changes. Independent checks can establish VERIFIED for formal goals;
ordinary informational answers may remain UNVERIFIED. A model's claim is not proof.
Read-only host tools are distinct from isolated workspace operations. This is not
unrestricted access to the host or root.

## Requirements and install

Target: Debian 13 x86-64, CPU-only, Python 3.11+, systemd user manager, rootless
namespaces, Landlock ABI >=3 and libseccomp2. tmux is optional. RAM needs depend on
GGUF/context: use a smaller model on smaller machines. 9B Q4_K_M is an optional
profile for suitable hardware, not a universal requirement. See [INSTALL.md](INSTALL.md).

```
sudo apt install ./neuroos_0.1.0~alpha1-1_amd64.deb
neuroos setup
neuroos doctor
neuroos doctor --probe
neuroos chat --plain
```

Obtain llama.cpp and compatible GGUF separately, with their licenses/checksums.
Internet is needed to obtain dependencies/weights unless already available, not
for normal local inference. Setup performs no automatic model download. Use
`neuroos chat` for tmux or `./neuroos chat --plain` from configured source.

## Examples of ordinary requests

- “Which Linux version is installed?”
- “Show the processes using the most memory. What is the second one?”
- “Create a small JSON summary from these files in the permitted workspace.”
- “Make an archive of this directory and check its contents.”
- “What happened to the previous task? Continue it.”

These are example intents, not guaranteed outcomes or hard-coded command recipes.
File authority and verification matter; an arbitrary host directory is not writable
merely because it was mentioned. Use `/status`, `/pause`, `/resume`, `/cancel`,
`/history`, `/details`, `/quit`. Long CPU responses can take minutes.

## Testing, feedback and contributions

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and
[VALIDATION.md](VALIDATION.md). `neuroos feedback` creates a local metadata-only
archive: review before sharing. Nothing is uploaded automatically. Never post raw
SQLite, chats, private paths, credentials or network identities in issues.

Project-owned code: [Apache License 2.0](LICENSE). Copyright 2026 DeAtH de FraG; see [NOTICE](NOTICE). Third-party tools and GGUFs
retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
