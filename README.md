# Git

## Meta AI link checker

`meta_ai_link_checker.py` checks whether Meta AI share links (or any URL)
are dead / returning a 404. It's a single-file script with no dependencies
beyond the Python standard library (Python 3.8+).

Meta AI's share pages (`https://www.meta.ai/share/a/<id>`) are a Next.js
app. When a shared conversation no longer exists, the server sometimes
still responds with HTTP 200 while streaming an embedded error marker
(`NEXT_HTTP_ERROR_FALLBACK;404`) inside the page's React payload, instead
of returning a real 404 status. A plain status-code check misses that
case, so this script checks both.

### Usage

```bash
# Check one or more links directly
python3 meta_ai_link_checker.py https://www.meta.ai/share/a/<id>

# Check a list of links from a file (one per line, # for comments)
python3 meta_ai_link_checker.py --file links.txt

# Machine-readable output
python3 meta_ai_link_checker.py --json https://www.meta.ai/share/a/<id>
```

Exits with status `1` if any checked link is dead, `0` otherwise — handy
for scripting or CI.
