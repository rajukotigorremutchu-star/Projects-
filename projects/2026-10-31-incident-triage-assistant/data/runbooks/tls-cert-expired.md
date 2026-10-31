# TLS certificate expired
## Symptoms
Clients fail with "certificate has expired", "x509: certificate verify failed" or SSL handshake errors. Browsers show a privacy warning.
## Mitigation
1. Inspect the served certificate with `openssl s_client -connect host:443`.
2. Renew via the certificate manager or ACME client and reload the ingress or load balancer.
3. Verify the full chain including intermediates is served.
4. Add expiry monitoring with a 21 day warning.
