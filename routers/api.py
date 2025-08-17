from fastapi import APIRouter
from routers.v1 import task, orsdm

v1_router = APIRouter()
v1_router.include_router(task.router, prefix="/api", tags=["SCM"])
v1_router.include_router(orsdm.router, prefix="/api", tags=["ODM"])
