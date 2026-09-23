"""`pbapi`: run the FastAPI service with uvicorn.

    uv run pbapi                    # http://127.0.0.1:8000
    uv run pbapi --reload           # reload on source changes

Bound to the loopback interface by default. There is no authentication yet, so
an API listening on 0.0.0.0 would hand the whole upload endpoint to anything
else on the network.
"""

import argparse

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pbapi", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    uvicorn.run(
        "pickleball_api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
