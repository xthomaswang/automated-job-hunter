#!/usr/bin/env python3
"""Entry point for the automated-job-hunter engine: `python3 hunter.py <command>`.

Needs only Python 3.9 or newer and its standard library.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ajh.cli import main

if __name__ == "__main__":
    sys.exit(main())
