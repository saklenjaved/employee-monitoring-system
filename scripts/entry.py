"""PyInstaller entry script. PyInstaller cannot run `python -m`, so this file calls the same main()."""

import sys

from winagent.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
