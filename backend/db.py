"""MongoDB client ownership and collection accessors."""

from pymongo import AsyncMongoClient

from config import DB_NAME, MONGO_URI

_client: AsyncMongoClient | None = None


async def connect() -> None:
    """Open MongoDB and verify that it is reachable."""
    global _client
    _client = AsyncMongoClient(MONGO_URI)
    try:
        await _client.admin.command("ping")
    except Exception as exc:
        await _client.close()
        _client = None
        raise RuntimeError(f"Unable to connect to MongoDB: {exc}") from exc


async def disconnect() -> None:
    """Close the shared MongoDB client."""
    global _client
    if _client is None:
        return
    await _client.close()
    _client = None


def channels_collection():
    """Return the channel registry collection."""
    if _client is None:
        raise RuntimeError("MongoDB is not connected")
    return _client[DB_NAME]["channels"]


def settings_collection():
    """Return the application settings collection."""
    if _client is None:
        raise RuntimeError("MongoDB is not connected")
    return _client[DB_NAME]["settings"]


def postdata_collection():
    """Return the postData1 forward-log collection."""
    if _client is None:
        raise RuntimeError("MongoDB is not connected")
    return _client[DB_NAME]["postData1"]


def playability_collection():
    """Return the playability verdict collection."""
    if _client is None:
        raise RuntimeError("MongoDB is not connected")
    return _client[DB_NAME]["playability"]
