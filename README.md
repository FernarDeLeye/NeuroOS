# NeuroOS 0.1.0-alpha.2

**Offline conversational Linux agent** — describe a goal in ordinary language, let a
local model select tools, and inspect what actually happened. NeuroOS is an
application on Linux, not a new kernel, distribution or hosted AI service. This is
a limited alpha for independent testing, not critical administration.

## What changed in Alpha 2

The context manager now preserves an unchanged prompt prefix when the exact local
tokenizer says the prompt plus output budget fits. It compacts repeated metadata and
uses structured references to eligible delivered observations. Full dialogue, object
identity and original evidence remain in SQLite. Alpha 1 already included durable
object binding for processes, files and network observations; these mechanisms are
retained.

On one sequential Lenovo X230 run with Qwen3.5 9B Q4_K_M, context 8192 and
non-thinking inference, warm median prefill fell 39.1% (216.14 to 131.57 seconds),
warm full-response latency fell 36.2% (232.34 to 148.14 seconds), and newly
evaluated input tokens fell from 968.5 to 554.5. The total seven-turn conversation
was 24.6% faster. These machine/workload-specific results are not guarantees; cold
first requests remained slow. See RELEASE_NOTES.md and KNOWN_ISSUES.md.

## Architecture and safety

`Terminal/tmux → conversation & presentation → model adapter → tools/observations`

Execution uses rootless workspace isolation; SQLite stores conversations/tasks; a
controlled publisher applies authorized changes; independent checks can establish
VERIFIED only for supported formal conditions. A model's claim is not proof. The
program does not provide unrestricted host or root access.

## Requirements and install

Debian 13 amd64, CPU-only, Python 3.11+, systemd user manager, rootless namespaces,
Landlock ABI >=3 and libseccomp2. tmux is optional. RAM needs depend on model and
context. Qwen3.5 9B Q4_K_M is optional for suitable systems, not required on every
host. See INSTALL.md and third-party notices. No weights or inference binary ship
in the package; obtain and license them separately.

```sh
sudo apt install ./neuroos_0.1.0~alpha2-1_amd64.deb
neuroos setup
neuroos doctor
neuroos doctor --probe
neuroos chat --plain
```

Setup does not download multi-gigabyte models. After configuring a compatible local
llama.cpp server and GGUF, ordinary inference works offline.

## Examples

- “Which Linux version is installed?”
- “Show the processes using the most memory. What is the second one?”
- “Create a JSON summary from these files in the permitted workspace.”
- “Make an archive of this directory and check its contents.”
- “What happened to the previous task? Continue it.”

These are intents, not guaranteed outcomes or fixed command recipes. Use `/status`,
`/pause`, `/resume`, `/cancel`, `/history`, `/details` and `/quit`. Read
CONTRIBUTING.md and SECURITY.md. `neuroos feedback` produces a local diagnostic
archive for review; nothing is sent automatically.

Project code is under Apache-2.0; copyright attribution is in NOTICE. External tools
and model weights retain their own licenses; see THIRD_PARTY_NOTICES.md.
