#!/usr/bin/env python3
"""Extract OK API flows from mitmproxy capture for analysis."""
import sys
from mitmproxy import io as mio
from mitmproxy.exceptions import FlowReadException


def main(path: str, host_filter: str = "ok.dk"):
    seen = set()
    with open(path, "rb") as f:
        try:
            reader = mio.FlowReader(f)
            for flow in reader.stream():
                if not hasattr(flow, "request"):
                    continue
                req = flow.request
                if host_filter not in req.host:
                    continue

                key = (req.method, req.pretty_url.split("?")[0])
                if key in seen:
                    continue
                seen.add(key)

                print("=" * 80)
                print(f"{req.method} {req.pretty_url}")
                print("-- request headers --")
                for k, v in req.headers.items():
                    if k.lower() in ("authorization", "cookie", "x-api-key", "x-auth-token"):
                        print(f"  {k}: {v[:80]}{'...' if len(v) > 80 else ''}")
                    else:
                        print(f"  {k}: {v}")
                if req.content:
                    body = req.get_text(strict=False) or ""
                    print("-- request body --")
                    print(body[:2000])
                if flow.response:
                    print(f"-- response: {flow.response.status_code} --")
                    body = flow.response.get_text(strict=False) or ""
                    print(body[:1500])
                print()
        except FlowReadException as e:
            print(f"Flow read error: {e}", file=sys.stderr)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "flows.mitm"
    host = sys.argv[2] if len(sys.argv) > 2 else "ok.dk"
    main(path, host)
