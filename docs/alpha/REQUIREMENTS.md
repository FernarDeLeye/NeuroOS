# Requirements

Supported: Debian13 amd64 with systemd and an ordinary logged-in user manager.
Python>=3.11; Debian13 supplies its supported Python. CPU-only; no GPU required.
Rootless namespaces, LandlockABI>=3, libseccomp2. Dependencies are in debian/control.
Optional tmux for layout; --plain works without it. NetworkManager only for its
read-only device/Wi-Fi observations; not required for file/chat operations.

There is no single valid RAM minimum independent of GGUF/context. Software alone
is small, but the backend must fit the weights, KV cache, working buffers, sandbox
workers and Linux reserve. Budget at least several GiB for small quantized models;
9B Q4_K_M is recommended only on16–32GiB machines after admission/real measurement.
16GiB is not assumed for all testers. Context/output settings are chosen in setup.
Leave free disk for your GGUF, private SQLite, workspaces and artifacts; no weights
are inside the small .deb. No automatic multi-GB network transfers occur.

Package build tools: debhelper13,dh-python,dpkg-dev,python3. Network is needed for
apt dependencies and separately obtaining backend/weights, not normal offline use.
