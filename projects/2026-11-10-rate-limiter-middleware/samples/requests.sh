#!/usr/bin/env bash
# With `uvicorn app:app` running: 6 quick searches; the 6th returns 429 + Retry-After.
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -D - "localhost:8000/search?q=x" | grep -iE "^(HTTP|ratelimit|retry)"; echo; done
