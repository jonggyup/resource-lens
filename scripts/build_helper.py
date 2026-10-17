"""Build an isolated zipapp; installation and exact-unit policy are explicit admin steps."""
from pathlib import Path
import argparse
import shutil
import tempfile
import zipapp
parser = argparse.ArgumentParser()
parser.add_argument('output')
args = parser.parse_args()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    shutil.copytree(Path(__file__).resolve().parents[1]/'resourcelens', root/'resourcelens', ignore=shutil.ignore_patterns('__pycache__'))
    zipapp.create_archive(root, args.output, interpreter='/usr/bin/python3 -I', main='resourcelens.helper:main')
print('Built',args.output,'— see docs/HELPER.md before installing')
