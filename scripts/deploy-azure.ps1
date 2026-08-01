[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,
    [string]$Location = "centralindia",
    [string]$ResourceGroup = "recliq-prod-rg",
    [string]$NamePrefix = "recliq"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Get-PlainTextSecret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Invoke-AzJson([string[]]$Arguments) {
    $result = & az @Arguments --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI command failed: az $($Arguments -join ' ')" }
    return ($result | ConvertFrom-Json)
}

az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) { throw "Unable to select Azure subscription $SubscriptionId" }

az extension add --name containerapp --upgrade --only-show-errors
az group create --name $ResourceGroup --location $Location --only-show-errors | Out-Null

$suffix = (Get-Date).ToUniversalTime().ToString("MMddHHmm")
$acrName = "$NamePrefix$($suffix.ToLower())acr".Replace("-", "")
$postgresName = "$NamePrefix-$suffix-pg".ToLower()
$storageName = "$NamePrefix$($suffix.ToLower())st".Replace("-", "")
$containerEnv = "$NamePrefix-$suffix-env".ToLower()
$backendName = "$NamePrefix-backend".ToLower()
$frontendName = "$NamePrefix-frontend".ToLower()
$databaseAdmin = "recliqadmin"
$databasePassword = Get-PlainTextSecret "Create the Azure PostgreSQL admin password"

az acr create --resource-group $ResourceGroup --name $acrName --sku Basic --admin-enabled true --location $Location --only-show-errors | Out-Null
az acr build --registry $acrName --image "$NamePrefix-backend:latest" --file docker/backend.azure.Dockerfile $repoRoot --only-show-errors

az postgres flexible-server create `
    --resource-group $ResourceGroup `
    --name $postgresName `
    --location $Location `
    --admin-user $databaseAdmin `
    --admin-password $databasePassword `
    --sku-name Standard_B1ms `
    --tier Burstable `
    --storage-size 32 `
    --version 16 `
    --public-access 0.0.0.0 `
    --only-show-errors | Out-Null

az postgres flexible-server db create `
    --resource-group $ResourceGroup `
    --server-name $postgresName `
    --database-name recliq `
    --only-show-errors | Out-Null

az storage account create `
    --resource-group $ResourceGroup `
    --name $storageName `
    --location $Location `
    --sku Standard_LRS `
    --kind StorageV2 `
    --https-only true `
    --only-show-errors | Out-Null

$storageConnection = (& az storage account show-connection-string --resource-group $ResourceGroup --name $storageName --only-show-errors --query connectionString --output tsv)
if ($LASTEXITCODE -ne 0) { throw "Unable to obtain the Azure Storage connection string" }
az storage container create --name recliq --connection-string $storageConnection --only-show-errors | Out-Null

$acr = Invoke-AzJson @("acr", "show", "--resource-group", $ResourceGroup, "--name", $acrName)
$acrCredentials = Invoke-AzJson @("acr", "credential", "show", "--resource-group", $ResourceGroup, "--name", $acrName)
$acrPassword = $acrCredentials.passwords[0].value
$databaseUrl = "postgresql+psycopg://${databaseAdmin}:$([uri]::EscapeDataString($databasePassword))@$postgresName.postgres.database.azure.com:5432/recliq?sslmode=require"

az containerapp env create `
    --name $containerEnv `
    --resource-group $ResourceGroup `
    --location $Location `
    --only-show-errors | Out-Null

az containerapp create `
    --name $backendName `
    --resource-group $ResourceGroup `
    --environment $containerEnv `
    --image "$($acr.loginServer)/$NamePrefix-backend:latest" `
    --registry-server $acr.loginServer `
    --registry-username $acrCredentials.username `
    --registry-password $acrPassword `
    --target-port 8000 `
    --ingress external `
    --min-replicas 1 `
    --max-replicas 2 `
    --secrets "database-url=$databaseUrl" "acr-password=$acrPassword" "storage-connection=$storageConnection" `
    --env-vars "DATABASE_URL=secretref:database-url" "STORAGE_BACKEND=azure" "AZURE_STORAGE_CONNECTION_STRING=secretref:storage-connection" "AZURE_STORAGE_CONTAINER=recliq" "SESSION_COOKIE_SECURE=true" `
    --only-show-errors | Out-Null

$backend = Invoke-AzJson @("containerapp", "show", "--name", $backendName, "--resource-group", $ResourceGroup)
$backendUrl = "https://$($backend.properties.configuration.ingress.fqdn)"

az acr build `
    --registry $acrName `
    --image "$NamePrefix-frontend:latest" `
    --file docker/frontend.azure.Dockerfile `
    --build-arg "VITE_API_BASE_URL=$backendUrl/api" `
    $repoRoot `
    --only-show-errors

az containerapp create `
    --name $frontendName `
    --resource-group $ResourceGroup `
    --environment $containerEnv `
    --image "$($acr.loginServer)/$NamePrefix-frontend:latest" `
    --registry-server $acr.loginServer `
    --registry-username $acrCredentials.username `
    --registry-password $acrPassword `
    --target-port 80 `
    --ingress external `
    --min-replicas 1 `
    --max-replicas 2 `
    --only-show-errors | Out-Null

$frontend = Invoke-AzJson @("containerapp", "show", "--name", $frontendName, "--resource-group", $ResourceGroup)
$frontendUrl = "https://$($frontend.properties.configuration.ingress.fqdn)"

az containerapp update `
    --name $backendName `
    --resource-group $ResourceGroup `
    --set-env-vars "FRONTEND_ORIGIN=$frontendUrl" `
    --only-show-errors | Out-Null

Write-Host ""
Write-Host "RecliQ deployment complete" -ForegroundColor Green
Write-Host "Website: $frontendUrl"
Write-Host "API:     $backendUrl"
Write-Host "Resource group: $ResourceGroup"
