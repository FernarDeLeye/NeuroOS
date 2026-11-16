import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from neuro_next.distribution_cli import main
main()
