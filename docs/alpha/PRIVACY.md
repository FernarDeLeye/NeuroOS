# Feedback

`neuroos feedback` creates a LOCAL0600 tar.gz; nothing is uploaded. Inspect with
`tar -tzf PATH` and `tar -xOf PATH report.json` before sharing voluntarily.

Allowlisted: product/Python/platform version, OS version ID, utility presence,
backend kind, numeric inference profile and configured memory cap. No username,
hostname, model path, endpoint, interfaces, SSIDs, environment, private keys,
tokens, passwords, personal files, model weights or conversation contents.

Optional `neuroos feedback --include-traces TASK_ID`: explicit sanitized event-kind,
token-count/timing metadata only. It NEVER adds prompts, replies, commands, stdout,
stderr, exception text or raw SQLite. Detailed content-bearing traces are deliberately
not exported by the alpha. `--output PATH` refuses overwrite/symlinks. Review every
archive; share it manually with the project owner, never by an automatic upload.
