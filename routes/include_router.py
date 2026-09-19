from fastapi import FastAPI

from routes.user import router as user_router

ROUTERS = (user_router,)


def include_app_routers(app: FastAPI) -> None:
    """Register every router from the ROUTERS tuple on the app."""
    for router in ROUTERS:
        app.include_router(router)
