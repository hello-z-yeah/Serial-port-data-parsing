"""支持 python -m protocol_parser 调用 CLI。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
