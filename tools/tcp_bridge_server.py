#!/usr/bin/env python3
"""MeshCore TCP Bridge Server — entry point. See bridge_server/ for implementation."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bridge_server.cli import run_cli

if __name__ == "__main__":
    run_cli()
