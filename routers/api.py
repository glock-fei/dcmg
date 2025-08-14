from fastapi import APIRouter
from routers.v1 import task

v1_router = APIRouter()
v1_router.include_router(task.router, prefix="/api", tags=["SCM"])
