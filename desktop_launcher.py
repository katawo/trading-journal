"""PyInstaller entry point for the portable Trading Journal desktop bundle."""

from trading_journal.desktop import main


if __name__ == "__main__":
    raise SystemExit(main())
