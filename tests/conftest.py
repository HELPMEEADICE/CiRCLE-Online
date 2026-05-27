import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def db_cleanup():
    yield
    import backend.database as db_mod
    if db_mod._db:
        await db_mod._db.close()
