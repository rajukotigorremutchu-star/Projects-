# Deployment Rollback

## When to roll back
Roll back immediately if the error rate exceeds 2 percent for 5 minutes after a deploy, or if the canary fails its health checks. Do not wait for root cause analysis before rolling back; investigate afterwards.

## Rolling back with Argo CD
Open the application in Argo CD, choose History and Rollback, and select the last healthy revision. Alternatively run `argocd app rollback <app> <revision>`. Rollback takes about 3 minutes for a standard service. Automated sync must be disabled first or Argo CD will redeploy the bad revision.

## Database migrations
Migrations are forward-only and are never rolled back automatically. If a release included a migration, check that the previous application version is compatible with the new schema before rolling back. Expand-and-contract migrations make this safe.
