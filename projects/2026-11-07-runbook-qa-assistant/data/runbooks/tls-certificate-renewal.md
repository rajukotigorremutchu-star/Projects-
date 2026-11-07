# TLS Certificate Renewal

## Automatic renewal
Certificates are issued by cert-manager using the Let's Encrypt ACME issuer. Renewal starts 30 days before expiry. Each certificate is stored as a Kubernetes secret in the namespace of the ingress that uses it.

## When renewal fails
Run `kubectl describe certificate <name>` and read the events. The most common cause is a failed HTTP-01 challenge because the ingress blocks the `/.well-known/acme-challenge/` path. Rate limits are the second most common cause: Let's Encrypt allows 5 duplicate certificates per week.

## Emergency manual issue
If a certificate expires in under 48 hours and automatic renewal is broken, delete the failing `CertificateRequest` object so cert-manager retries. As a last resort, request a certificate from the internal CA with `make issue-internal-cert HOST=<hostname>`, which is valid for 7 days only.
