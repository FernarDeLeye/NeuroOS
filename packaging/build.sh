#!/bin/sh
set -eu
revision=${1:-HEAD}
out=${2:-/tmp/neuroos-public-build}
commit=$(git rev-parse "$revision^{commit}")
export SOURCE_DATE_EPOCH=$(git show -s --format=%ct "$commit")
[ ! -e "$out/source" ] || { echo 'Choose a fresh build output directory.' >&2; exit 2; }
mkdir -p "$out/source"
git archive "$commit" | tar -x -C "$out/source"
python3 - "$out/source/release.json" "$commit" <<'INNER'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);c=json.loads(p.read_text());c['source_commit']=sys.argv[2];p.write_text(json.dumps(c,sort_keys=True,separators=(',',':'))+'\n')
INNER
cd "$out/source"
if [ "${NEUROOS_EXTRACTED_BUILD_TOOLS:-}" = 1 ]; then dpkg-buildpackage -us -uc -b -d
else dpkg-buildpackage -us -uc -b; fi
