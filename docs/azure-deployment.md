# Azure Deployment

## Container Apps Quick Deployment

From the repository root after installing Azure CLI:

```powershell
az login
az account list --output table
./scripts/deploy-azure.ps1 -SubscriptionId <your-subscription-id>
```

The script creates an Azure Container Registry, PostgreSQL Flexible Server, Container Apps environment, backend API, and frontend website. It prints the public website URL when finished. The database password is entered interactively and is not stored in the repository.

Recommended Azure services:

- Azure Container Apps or Azure App Service for the backend container.
- Azure Static Web Apps, App Service, or containerized Nginx for the frontend.
- Azure Database for PostgreSQL Flexible Server.
- Azure Blob Storage for uploaded files and generated reports.
- Azure Key Vault for database and storage secrets.
- Azure Monitor and Application Insights for logs, metrics, and traces.

## Backend Environment Variables

```env
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database>
STORAGE_BACKEND=azure
AZURE_STORAGE_CONNECTION_STRING=<key-vault-secret>
AZURE_STORAGE_CONTAINER=recliq
FRONTEND_ORIGIN=https://<frontend-domain>
```

## Deployment Steps

1. Build and push backend and frontend images to Azure Container Registry.
2. Provision PostgreSQL Flexible Server and run `alembic upgrade head`.
3. Provision Blob Storage and create the `reconx` container.
4. Store secrets in Key Vault and expose them to the backend app.
5. Configure CORS with the deployed frontend origin.
6. Enable Application Insights and container logs.

## Production Notes

- Replace FastAPI `BackgroundTasks` with Celery, RQ, Azure Queue Storage, or Azure Service Bus when jobs become long-running or high-volume.
- Use managed identity instead of connection strings for Blob Storage where possible.
- This MVP has no login authentication. Anonymous session isolation is enabled for multiple simultaneous visitors. Set `SESSION_COOKIE_SECURE=true` when serving over HTTPS and plan a future account-linking flow before requiring durable user-owned workspaces.
