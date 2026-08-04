#!/usr/bin/env python3
"""Check whether Meta AI share links (or any URL) are dead / returning 404.

Meta AI's share pages (https://www.meta.ai/share/a/<id>) are served by a
Next.js app. When a shared conversation no longer exists, the server
sometimes still responds with HTTP 200 and streams an embedded error
marker (``NEXT_HTTP_ERROR_FALLBACK;404``) inside the page's React payload
instead of returning a real 404 status. A plain status-code check misses
that case, so this tool checks both the HTTP status and the response body.

Usage:
    python3 meta_ai_link_checker.py https://www.meta.ai/share/a/<id> ...
    python3 meta_ai_link_checker.py --file links.txt
    python3 meta_ai_link_checker.py --json https://example.com
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import List, Optional

NEXT_404_MARKER = "NEXT_HTTP_ERROR_FALLBACK;404"
DEFAULT_TIMEOUT = 10.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; MetaAILinkChecker/1.0; "
    "+https://github.com/tonystark4444/git)"
)


@dataclass
class LinkStatus:
    url: str
    http_status: Optional[int]
    dead: bool
    reason: str


def check_url(url: str, timeout: float = DEFAULT_TIMEOUT) -> LinkStatus:
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
    except urllib.error.URLError as exc:
        return LinkStatus(url=url, http_status=None, dead=True, reason=f"Request failed: {exc.reason}")

    if status == 404:
        return LinkStatus(url=url, http_status=status, dead=True, reason="HTTP 404")

    if NEXT_404_MARKER in body:
        return LinkStatus(
            url=url,
            http_status=status,
            dead=True,
            reason="Page streamed a Next.js 404 fallback (link expired or removed)",
        )

    return LinkStatus(url=url, http_status=status, dead=False, reason="OK")


def load_urls(args: argparse.Namespace) -> List[str]:
    urls = list(args.urls)
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            urls.extend(line.strip() for line in handle if line.strip() and not line.startswith("#"))
    return urls


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check Meta AI share links (or any URL) for 404s.")
    parser.add_argument("urls", nargs="*", help="URLs to check")
    parser.add_argument("--file", "-f", help="File with one URL per line (# lines are comments)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args(argv)

    urls = load_urls(args)
    if not urls:
        parser.error("No URLs provided. Pass URLs as arguments or use --file.")

    results = [check_url(url, timeout=args.timeout) for url in urls]

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for result in results:
            marker = "DEAD" if result.dead else "OK"
            status_display = result.http_status if result.http_status is not None else "-"
            print(f"[{marker}] {status_display} {result.url} - {result.reason}")

    return 1 if any(r.dead for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
