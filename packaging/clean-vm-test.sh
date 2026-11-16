#!/bin/sh
# Run INSIDE a disposable Debian13 VM, NEVER on the any development host.
set -eu
if [ "${NEUROOS_CLEAN_VM:-}" != YES ] || [ -n "${NEUROOS_PROTECTED_HOST:-}" ]; then
  echo 'Refusing: set NEUROOS_CLEAN_VM=YES only inside a disposable clean VM.' >&2; exit 2
fi
if ! systemd-detect-virt --vm --quiet && ! systemd-detect-virt --container --quiet; then
  echo 'No VM/container detected. Do not run on development/production host.' >&2; exit 2
fi
if [ "$(id -u)" = 0 ]; then echo 'Run as a logged-in ordinary tester with sudo for apt only.' >&2; exit 2; fi
package=$(realpath "${1:?Pass the alpha .deb}")
sudo apt-get update
sudo apt-get install "$package"
neuroos --version
neuroos setup
neuroos doctor
neuroos doctor --probe
cat <<'EOF'
Live model check (not a mock): in the next chat ask a system-version question,
then ask to create alpha-check.txt containing alpha-test in the private workspace.
Do not supply commands. Check the observed OS against /etc/os-release and inspect
the actual published file independently. If model fails, record MODEL_FAILURE;
a successful install is not proof of model quality. Use /pause during a live task,
/quit, reopen chat, /history, /resume and /status. Confirm state/artifacts survive.
EOF
neuroos chat --plain
printf 'Confirm actual OS/file/state/resume checks passed (type YES): '
read -r confirmed
[ "$confirmed" = YES ] || exit 3
checkdir=$(mktemp -d)
# Metadata-only upgrade tests dpkg lifecycle, not a new runtime/schema migration.
dpkg-deb -R "$package" "$checkdir/image"
sed -i 's/^Version: .*/Version: 0.1.0~alpha1-1+upgradecheck1/' "$checkdir/image/DEBIAN/control"
dpkg-deb --root-owner-group -b "$checkdir/image" "$checkdir/upgrade.deb"
printf 'Back up selected private config/data first; no paths are deleted by this script. Continue? YES: '
read -r confirmed
[ "$confirmed" = YES ] || exit 3
sudo apt-get install "$checkdir/upgrade.deb"
neuroos doctor
neuroos chat --plain
printf 'Confirm history/artifacts survived metadata upgrade (YES): '
read -r confirmed
[ "$confirmed" = YES ] || exit 3
# Only user's optional backend, never a production/research router.
printf 'If managed mode was chosen, stop it with neuroos backend stop now. Continue? YES: '
read -r confirmed
[ "$confirmed" = YES ] || exit 3
sudo apt-get remove neuroos
printf 'Verify config, history, artifacts and model remain. They are not purged automatically.\n'
printf 'Repeat apt install and optionally apt purge to check package cleanup; inspect units.\n'
printf 'Test root: %s (remove manually after review)\n' "$checkdir"
