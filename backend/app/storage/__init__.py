from app.core.config import get_settings
from app.storage.azure_blob import AzureBlobStorage
from app.storage.local import LocalStorage


def get_storage():
    settings = get_settings()
    if settings.storage_backend.lower() == "azure":
        return AzureBlobStorage(settings)
    return LocalStorage(settings)

