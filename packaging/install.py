"""Install only portable runtime and metadata; never developer state."""
import shutil,sys
from pathlib import Path
root=Path(sys.argv[1]);app=root/'usr/lib/neuroos';app.mkdir(parents=True,exist_ok=True)
shutil.copytree('neuro_next',app/'neuro_next',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for name in ['INSTALLATION','release.json']:shutil.copyfile(name,app/name)
shutil.copyfile('packaging/entry.py',app/'entry.py')
bin=root/'usr/bin';bin.mkdir(parents=True,exist_ok=True)
shutil.copyfile('packaging/neuroos',bin/'neuroos');(bin/'neuroos').chmod(0o755)
