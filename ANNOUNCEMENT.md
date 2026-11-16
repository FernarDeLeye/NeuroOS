# Draft announcement — publish only after final approval

**NeuroOS: an early offline conversational Linux agent (0.1.0-alpha.1)**

I'm preparing a small open-source alpha of NeuroOS: a text interface where a local
LLM chooses Linux tools, observes results and tries to complete your request. It
isn't a new distro or a cloud service. It runs on Debian13 x86-64, supports a local
llama.cpp/GGUF backend and keeps conversations/tasks in local SQLite.

The current focus is practical Linux conversation, rootless command execution,
controlled file publication, independent checks and bounded repair—not benchmark
leaderboards. CPU-only is supported, but responses can take minutes and model
quality varies. This is experimental and not for critical administration.

I'm looking for Linux/self-hosting testers and LocalLLaMA feedback on usability,
model compatibility, reproducible failures and safety boundaries. Source is
Apache-2.0; weights are separate. Clean second-machine installation validation is
still pending and documented. No automatic telemetry or model downloads.

Repository/release link: [INSERT AFTER OWNER-APPROVED PUBLICATION]
Please read INSTALL, KNOWN_ISSUES and SECURITY before trying it. Synthetic bug
reports and reviewed metadata-only feedback are especially useful.
