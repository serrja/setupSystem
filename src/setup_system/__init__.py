"""Debian workstation setup."""

import sys

# Opening the read-only UI must not create an import cache in the user runtime.
sys.dont_write_bytecode = True
