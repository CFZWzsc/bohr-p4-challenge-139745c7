#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin wrapper: delegates to sk_entry.py with the same CLI."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sk_entry import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
