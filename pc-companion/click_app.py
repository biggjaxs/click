"""Entry point for the packaged Click app (Click.app / Click.exe)."""
import sys

from companion.app import main

if __name__ == "__main__":
    sys.exit(main())
