# Azure deployment

RecliQ deploys to **Azure Container Apps** with a script that creates everything it needs. This page explains what the script does, how to make the deployment production-grade, and how to update it.

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

## Making it production-grade

The script gives a working deployment. For real customer data, add:

1. **A durable job queue.** The script leaves `JOB_QUEUE_BACKEND=local`, so runs execute inside the API replicas; a run in progress is re-queued if its replica restarts, but long runs compete with API requests. Add Azure Cache for Redis, set `JOB_QUEUE_BACKEND=redis` and `REDIS_URL`, and create a second Container App from the backend image with the command `python -m app.jobs.worker`.
2. **Key Vault.** Move the secrets into Azure Key Vault and reference them from the Container Apps with a managed identity, instead of Container App secrets.
3. **Managed identity for storage and the registry.** Replace the storage connection string and the registry admin password with managed-identity access, and turn off the registry's admin user.
4. **Network rules.** The script opens PostgreSQL to Azure services (`0.0.0.0`). Restrict it with a virtual network or private endpoint.
5. **Monitoring.** Send container logs to Log Analytics, set `SENTRY_DSN` for error tracking, and alert on `/health/ready` failures. Logs are JSON with a request ID and job ID on every line.
6. **Limits and retention.** Review `MAX_UPLOAD_MB`, `MAX_ROWS_PER_SHEET` and `JOB_TIMEOUT_SECONDS` for your customers' file sizes.

## Environment variables

The backend reads these (full list in [backend/.env.example](../backend/.env.example)):

```env
DATABASE_URL=postgresql+psycopg://<user>:<password>@<server>.postgres.database.azure.com:5432/recliq?sslmode=require
STORAGE_BACKEND=azure
AZURE_STORAGE_CONNECTION_STRING=<from a secret>
AZURE_STORAGE_CONTAINER=recliq
FRONTEND_ORIGIN=https://<frontend-address>
SESSION_COOKIE_SECURE=true
JOB_QUEUE_BACKEND=redis            # with a worker app
REDIS_URL=rediss://<cache>.redis.cache.windows.net:6380/0
SENTRY_DSN=<optional>
```

The frontend's API address is fixed at build time through the `VITE_API_BASE_URL` build argument.

## Updating a deployment

Rebuild the images and point the apps at them:

```powershell
az acr build --registry <acr-name> --image recliq-backend:latest --file docker/backend.azure.Dockerfile .
az containerapp update --name <backend-app> --resource-group recliq-prod-rg --image <acr-name>.azurecr.io/recliq-backend:latest
```

Do the same for the frontend (`docker/frontend.azure.Dockerfile`, passing `--build-arg VITE_API_BASE_URL=https://<backend-address>/api`). Database migrations run automatically when the backend starts. To roll back, point the app at the previous image tag or revision (`az containerapp revision list`).

## Other hosting options

- **Render** (`render.yaml`): the API on a free web service with a free PostgreSQL database. The free plan has no background workers and only temporary disk, so uploads and reports are lost on restart. Suitable for demos only.
- **Vercel** (`frontend/vercel.json`): hosts the frontend as a static site; set `VITE_API_BASE_URL` to the API's address in the Vercel project.
