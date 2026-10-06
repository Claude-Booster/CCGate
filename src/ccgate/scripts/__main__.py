"""Entry point for ccgate.scripts.miss_audit via python -m ccgate.scripts.miss_audit"""
import sys

from . import miss_audit

if __name__ == "__main__":
    miss_audit.main(sys.argv[1:])
