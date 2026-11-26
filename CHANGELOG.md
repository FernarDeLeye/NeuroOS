# Changelog

## 0.1.0-alpha.2

- Reduce redundant conversational prompt input while retaining full history and
  canonical observations in SQLite. Reuse the stable processed prefix when the
  local tokenizer confirms the actual input/output budget fits context.
- Compact repeated object metadata and represent eligible delivered observations
  by exact structured references; changed, uncertain, diagnostic and current-task
  evidence remains complete.
- Keep Alpha 1 structured object binding, local model selection, rootless execution,
  publication, independent verification and repair behavior.
- On one sequential X230 run, warm median prefill fell 39.1%, warm median full
  response latency fell 36.2%, and newly evaluated tokens fell from 968.5 to 554.5.
  These are machine- and workload-specific observations, not performance guarantees.

## 0.1.0-alpha.1

Initial standalone public-alpha preparation: portable local backend configuration,
terminal conversation/plain/tmux, persistent tasks, rootless execution, controlled
publication, independent verification, bounded repair and structured object binding.
