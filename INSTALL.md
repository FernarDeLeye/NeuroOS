# Installation and first use

## Target and prerequisites

Debian13 amd64, CPU-only, normal logged-in user with systemd user bus. Python>=3.11,
rootless user/mount/PID/network namespaces, LandlockABI>=3 and libseccomp2 are
required. No insecure sandbox fallback. Optional tmux; NetworkManager/nmcli only
for corresponding read-only observations. No SSH/network/boot/sudoers changes.

There is no fixed RAM minimum independent of model. Allow for GGUF, KV cache,
working buffers, task workers and Linux reserve. Small quantized models need
several GiB; 9B Q4_K_M typically calls for a16–32GiB-class host and actual admission.
Disk must hold your own GGUF, private state and outputs. A GPU is not required.

## Package

Check SHA256SUMS against the files received through a trusted release channel.
A checksum is not an authenticated signature. On a disposable test machine:

```
sha256sum -c SHA256SUMS
sudo apt install ./neuroos_0.1.0~alpha2-1_amd64.deb
neuroos setup
neuroos doctor
neuroos doctor --probe
neuroos chat --plain
```

Setup selects an existing GGUF, backend, local alias/endpoint, private data path,
context/output budget and memory cap. No JSON editing needed. No weights or backend
binary are bundled/downloaded. See docs/alpha/MODELS.md in source (installed as
/usr/share/doc/neuroos/MODELS.md) for manual backend setup.
Compatible llama.cpp must expose its slot/template/tokenize/schema APIs; a generic
OpenAI-compatible endpoint is insufficient. Model compatibility is not model quality.

External backend: already load the selected model/context, one slot, loopback only;
operator owns its resource limits and weight provenance. Optional managed backend:
choose an existing llama-server executable, explicitly start the bounded USER service
with setup or `neuroos backend start`; `status`/`stop` are available. No service is
auto-enabled at install, no privileged runtime. Never run the agent as root.

`doctor` checks platform/tools/config/GGUF header/endpoint/slot/context/resources/
user bus/sandbox features. `doctor --probe` sends one actual constrained request.
Header validation alone proves neither loader compatibility nor real inference.
If blocked, correct the reported cause; do not disable security to force a run.

## Source build

On a build/test machine explicitly install build dependencies:
`sudo apt install build-essential debhelper dh-python python3`.
In a clean clone: `packaging/build.sh v0.1.0-alpha.2 /tmp/neuroos-alpha2-build`.
In an extracted source archive: `dpkg-buildpackage -us -uc -b`.
The resulting .deb is in the build directory's parent. Do not install it over an
existing development deployment. Run unit tests with:
`python3 -m unittest discover -s packaging/tests -v`.

## Data, restart, upgrades and removal

Config: `${XDG_CONFIG_HOME:-~/.config}/neuroos/config.json`,0600.
Data: `${XDG_STATE_HOME:-~/.local/state}/neuroos`,0700, unless selected otherwise.
Expert override: NEUROOS_CONFIG pointing to a private config. History is in SQLite;
reopening chat restores the conversation. Task commands are documented by
`neuroos chat --help` and `neuroos list`.

Tasks use the user's systemd manager. After-last-logout persistence depends on host
login policy. No automatic linger: understand the implications and explicitly ask
an administrator if it is needed. This release does not claim a new logout test.

Before upgrade: pause tasks, close clients, back up private state/config; apt install
the new package. SQLite migration is additive, backs up legacy state, rejects newer
schema downgrade. Hot replacement while tasks run is unsupported.
Before removal, stop tasks and your managed backend, then `sudo apt remove neuroos`.
Remove/purge do not intentionally delete private config/data/GGUF/output. Deleting
those is a separate explicit manual decision after backup. Actual clean apt upgrade/
remove verification is still pending; see VALIDATION.md.
