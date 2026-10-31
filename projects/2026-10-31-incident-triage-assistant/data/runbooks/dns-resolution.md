# DNS resolution failures
## Symptoms
Errors such as "no such host", "Temporary failure in name resolution", NXDOMAIN or getaddrinfo ENOTFOUND for internal service names.
## Mitigation
1. Test resolution from an affected pod with `nslookup` and `dig`.
2. Check coredns pods are healthy and not throttled.
3. Verify the search domains and ndots setting in resolv.conf.
4. Restart coredns if the cache is poisoned and watch the error rate.
