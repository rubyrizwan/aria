from __future__ import annotations

import argparse

import uvicorn
from cryptography.fernet import Fernet

from app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ARIA.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "generate-key"),
        default="serve",
    )
    parser.add_argument(
        "--host",
        choices=("127.0.0.1", "0.0.0.0", "::1", "::"),
        default=settings.host,
    )
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()

    if args.command == "generate-key":
        print(Fernet.generate_key().decode())
        return

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    uvicorn.run("app.main:app", host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
