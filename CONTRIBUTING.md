# Contributing

Use this standalone public snapshot, not the private experimental Git history.
The intended scope is a local conversational interface to Linux. Avoid task-specific
answers, benchmark command recipes, cloud-runtime dependencies or expanded privilege.

1. Describe a reproducible user-visible problem using synthetic inputs.
2. Branch from main. Keep the model/runtime conditions documented and changes small.
3. Run `python3 -m unittest discover -s packaging/tests -v` and the public-tree audit.
4. Run only the minimal relevant integration check; label mocks and real inference.
5. Explain behavior, validation and remaining limitations in the pull request.

Do not commit credentials, GGUF, SQLite, histories, traces, dumps, IP/MAC/SSID lists,
private paths, or generated packages. Use the issue templates and metadata-only
feedback after manual review. Preserve independent verification and fail-closed
sandbox behavior. Discuss changes requiring new privileges before implementation.

Project-owned code uses Apache-2.0. Submit only code you have the right to contribute;
retain notices for third-party material and describe its origin/license. No CLA is
currently imposed. The owner attribution placeholder must be settled before public
publication. No guarantee of response time or acceptance is provided.
