"""Entry point for ccgate.scripts.miss_audit via python -m ccgate.scripts.miss_audit"""
from . import miss_audit
import sys

if __name__ == "__main__":
    miss_audit.main(sys.argv[1:])
