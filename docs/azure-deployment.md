# Azure deployment

RecliQ targets **Azure Container Apps**. The included PowerShell script is a first-time provisioning example; it creates a new registry, database, storage account, Container Apps environment and apps. It is not an update script for an existing production deployment. Review the resource names before running it, and do not rerun it against a resource group that already contains RecliQ services.

## Quick deployment

Install the Azure CLI, then from the repository root:

```powershell
az login
az account list --output table
./scripts/deploy-azure.ps1 -SubscriptionId <your-subscription-id>
```

Optional parameters: `-Location` (default `centralindia`), `-ResourceGroup` (default `recliq-prod-rg`) and `-NamePrefix` (default `recliq`). The script asks for a PostgreSQL administrator password; it is never written to disk or to the repository. When it finishes, it prints the website and API addresses.

## What the script creates

| Resource | Details |
|---|---|
| Resource group | Holds everything below |
| Container Registry (Basic) | Builds and stores the backend and frontend images (`docker/backend.azure.Dockerfile`, `docker/frontend.azure.Dockerfile`) |
| PostgreSQL Flexible Server 16 | Burstable B1ms, 32 GB, with a `recliq` database; connections use TLS (`sslmode=require`) |
| Storage account + `recliq` container | Uploads, reports and report data (`STORAGE_BACKEND=azure`) |
| Container Apps environment | Runs both apps |
| Backend Container App | Port 8000, 1 to 2 replicas. Runs `alembic upgrade head` on start, then the API |
| Frontend Container App | Nginx serving the built app, 1 to 2 replicas, built with the backend's address |

The database URL, the storage connection string and the registry password are stored as **Container App secrets** and passed to the backend by reference (`secretref:`), never as plain environment values. The backend is configured with `SESSION_COOKIE_SECURE=true` and a `FRONTEND_ORIGIN` pointing at the frontend app.

The provisioning example predates account workspaces: it does not configure `AUTH_CSRF_SECRET`, an email provider, a bootstrap administrator, or a durable Redis worker. Production accounts therefore remain unavailable until the operator supplies those secrets. Do not enable registration until email verification is configured. The script also uses `az acr build`; if ACR Tasks are unavailable in the subscription, build images locally with Docker and push them with `az acr login` / `docker push` instead.

## Making it production-grade

The script gives a working deployment. For real customer data, add:

1. **A durable job queue.** The script leaves `JOB_QUEUE_BACKEND=local`, so runs execute inside the API replicas; a run in progress is re-queued if its replica restarts, but long runs compete with API requests. Add Azure Cache for Redis, set `JOB_QUEUE_BACKEND=redis` and `REDIS_URL`, and create a second Container App from the backend image with the command `python -m app.jobs.worker`.
2. **Key Vault.** Move the secrets into Azure Key Vault and reference them from the Container Apps with a managed identity, instead of Container App secrets.
3. **Managed identity for storage and the registry.** Replace the storage connection string and the registry admin password with managed-identity access, and turn off the registry's admin user.
4. **Network rules.** The script opens PostgreSQL to Azure services (`0.0.0.0`). Restrict it with a virtual network or private endpoint.
5. **Monitoring.** Send container logs to Log Analytics, set `SENTRY_DSN` for error tracking, and alert on `/health/ready` failures. Logs are JSON with a request ID and job ID on every line.
6. **Limits and retention.** Review `MAX_UPLOAD_MB`, `MAX_ROWS_PER_SHEET` and `JOB_TIMEOUT_SECONDS` for your customers' file sizes.
7. **Authentication and email.** Store `AUTH_CSRF_SECRET` (at least 32 random characters), `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` and a base32 `BOOTSTRAP_ADMIN_TOTP_SECRET` as Container App secrets. Set `EMAIL_BACKEND=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `PUBLIC_APP_URL` and `PUBLIC_API_URL`; then enable `REGISTRATION_ENABLED=true`. Configure OAuth client secrets and exact callback URLs only for providers you intend to offer. `EMAIL_BACKEND=log` is for local development only.
8. **Migration safety.** Back up PostgreSQL and Blob Storage, test `alembic upgrade head` on a copy, then apply the migration once before shifting production traffic. The current backend container command runs Alembic on startup; with multiple replicas, coordinate the rollout so multiple instances do not race to migrate. Revisions `20261009_0001` and `20261010_0001` add account/policy structures and widen counters; `20261011_0002` adds the editable platform email sender setting. These migrations do not rewrite guest workspace ownership.

## Environment variables

The backend reads these (full list in [backend/.env.example](../backend/.env.example)):

```env
DATABASE_URL=postgresql+psycopg://<user>:<password>@<server>.postgres.database.azure.com:5432/recliq?sslmode=require
STORAGE_BACKEND=azure
AZURE_STORAGE_CONNECTION_STRING=<from a secret>
AZURE_STORAGE_CONTAINER=recliq
FRONTEND_ORIGIN=https://<frontend-address>
SESSION_COOKIE_SECURE=true
AUTH_CSRF_SECRET=<at-least-32-random-characters-from-a-secret-store>
AUTH_SESSION_DAYS=30
REGISTRATION_ENABLED=false             # enable only after SMTP verification works
EMAIL_BACKEND=disabled                 # set to smtp when configured
SMTP_HOST=<provider-host>
SMTP_PORT=587
SMTP_USERNAME=<provider-user>
SMTP_PASSWORD=<secret>
SMTP_FROM_EMAIL=<verified-sender>
PUBLIC_APP_URL=https://<frontend-address>
PUBLIC_API_URL=https://<backend-address>/api
JOB_QUEUE_BACKEND=redis            # with a worker app
REDIS_URL=rediss://<cache>.redis.cache.windows.net:6380/0
SENTRY_DSN=<optional>
```

The local default sender address is `jagdev.s65@gmail.com`. In the Super Admin console, open **Email management** to change the platform From address and send a test to the administrator's verified email. Configure SMTP credentials as backend secrets; for Gmail, use an App Password with two-step verification. A sender address alone does not enable email delivery.

Provision the Super Admin only through the secret store. The bootstrap password and TOTP secret are not application defaults. Social provider keys are optional; the providers remain unavailable until configured and enabled.

The frontend's API address is fixed at build time through the `VITE_API_BASE_URL` build argument.

## Updating a deployment after review

After you have reviewed and approved the local changes, build tagged images locally if ACR Tasks are unavailable, push them, and update the Container Apps. Do not use the first-time provisioning script to update existing resources:

```powershell
az acr login --name <acr-name>
docker build -f docker/backend.azure.Dockerfile -t <acr-name>.azurecr.io/recliq-backend:<reviewed-tag> .
docker push <acr-name>.azurecr.io/recliq-backend:<reviewed-tag>
az containerapp update --name <backend-app> --resource-group <resource-group> --image <acr-name>.azurecr.io/recliq-backend:<reviewed-tag>
```

Do the same for the frontend (`docker/frontend.azure.Dockerfile`, passing `--build-arg VITE_API_BASE_URL=https://<backend-address>/api`). Review the Alembic changes and take a database backup before the new backend starts, because its container command runs migrations automatically. To roll back, point the app at the previous image tag or revision (`az containerapp revision list`); a code rollback does not automatically reverse database migrations.

## Other hosting options

- **Render** (`render.yaml`): the API on a free web service with a free PostgreSQL database. The free plan has no background workers and only temporary disk, so uploads and reports are lost on restart. Suitable for demos only.
- **Vercel** (`frontend/vercel.json`): hosts the frontend as a static site; set `VITE_API_BASE_URL` to the API's address in the Vercel project.
