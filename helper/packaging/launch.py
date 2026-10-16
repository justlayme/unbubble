"""PyInstaller entry point (kept outside the package so relative imports work when frozen)."""

from unbubble.__main__ import main

if __name__ == "__main__":
    main()
