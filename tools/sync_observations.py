"""Copy the observer's notebook (../../docs/observations, outside this repository) into ./observations so it is versioned and pushed.

    python tools/sync_observations.py

The project folder is the source of truth; this snapshot is overwritten every time.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
SRC = os.path.normpath(os.path.join(REPO, "..", "..", "docs", "observations"))
DST = os.path.join(REPO, "observations")

if not os.path.isdir(SRC):
    sys.exit("source folder not found: " + SRC)
if os.path.isdir(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("desktop.ini"))
n = sum(len(f) for _, _, f in os.walk(DST))
print("synced %d files: %s -> %s" % (n, SRC, DST))
