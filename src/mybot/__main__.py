"""`python -m mybot` bzw. `uv run mybot`."""

from adbbot.app import main as run_app
from mybot.bootstrap import register


def main() -> None:
    raise SystemExit(run_app(register=register, prog="mybot"))


if __name__ == "__main__":
    main()
