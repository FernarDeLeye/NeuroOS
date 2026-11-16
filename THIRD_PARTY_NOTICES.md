# Third-party components and license review

The distributed source/payload contains the project's Python code and documentation,
not vendored third-party inference code, binaries, libraries or model weights. The
project owner explicitly selected Apache-2.0 for its own code. Ownership/attribution
is intentionally not guessed; complete NOTICE. No full copyright-provenance audit
can establish ownership merely from a secret scan or Git authorship.

| External component | License / relationship | Distribution here |
|---|---|---|
| llama.cpp | MIT, upstream license link below | Separate user install; no code/binary bundled |
| Official Qwen3.5-9B | Apache-2.0, upstream below | Optional separately acquired model; no weights |
| Other GGUF / quantizations | Exact publisher's terms must be checked | Not bundled or relicensed |
| CPython / standard library | PSF license stack, Debian python3 notices | System dependency |
| libseccomp2 | LGPL-2.1 (Debian notices); ctypes calls into system library | System dependency |
| libc | Debian glibc license notices, principally LGPL | System dependency |
| systemd/dbus-user-session | Multiple upstream licenses; see Debian copyright files | System executables/services dependency |
| bash, coreutils, procps, iproute2, util-linux | GPL/LGPL and component-specific terms; Debian notices govern | Separate system tools invoked by process |
| tmux | ISC-style upstream notices | Optional external UI tool |
| NetworkManager | GPL-2.0-or-later and component notices | Optional read-only CLI source |
| debhelper/dh-python/dpkg | Debian package copyright notices | Build tools, not runtime vendoring |

Using separately installed tools does not relicense them as Apache-2.0. No conflicting
vendored component was identified in the release tree. This is a scoped dependency
review, not a legal guarantee about all future contributions or arbitrary GGUF.
If bundling/linkage/distribution changes, review obligations again and retain notices.

Primary references checked during preparation:
- https://raw.githubusercontent.com/ggml-org/llama.cpp/master/LICENSE
- https://huggingface.co/Qwen/Qwen3.5-9B/raw/main/LICENSE
- https://www.apache.org/licenses/LICENSE-2.0.txt
- Installed Debian packages: /usr/share/doc/PACKAGE/copyright (exact version).

NOTICE is supplied as project attribution metadata, not as evidence of an upstream
NOTICE obligation. No upstream NOTICE-bearing code was intentionally vendored.
