# Upstream API rate limiting
## Symptoms
Calls to a third party API return HTTP 429 Too Many Requests or "rate limit exceeded" with a Retry-After header.
## Mitigation
1. Honor the Retry-After header and apply exponential backoff with jitter.
2. Check for a retry storm caused by a recent failure and pause the batch job.
3. Cache repeated lookups and coalesce duplicate requests.
4. Request a quota increase from the vendor if sustained.
