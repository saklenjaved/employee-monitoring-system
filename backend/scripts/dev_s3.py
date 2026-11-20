"""A local stand-in for Amazon S3, for development only.

It runs moto's S3 emulator over HTTPS with the demo certificate, because the Windows agent only
uploads over HTTPS. Data is kept in memory and is lost when it stops. It does not check request
signatures, so it is not a test of S3's own security. Production uses the real private S3 bucket.

    python scripts/dev_s3.py --cert certs/cert.pem --key certs/key.pem

On Windows, stop it with `taskkill /PID <pid> /T /F` so its worker process stops too.
"""
import argparse

from moto.server import main as moto_main


def main():
    parser = argparse.ArgumentParser(description="Local S3 stand-in over HTTPS (development only)")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)  # 9000 is often taken by other projects
    args = parser.parse_args()
    print(f"Local S3 stand-in on https://{args.host}:{args.port}  (Ctrl+C to stop)", flush=True)
    moto_main(["-H", args.host, "-p", str(args.port), "-c", args.cert, "-k", args.key])


if __name__ == "__main__":
    main()
