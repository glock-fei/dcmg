from fastapi import APIRouter
from routers.v1 import task, orsdm, vdcm, sampling

v1_router = APIRouter()
v1_router.include_router(task.router, prefix="/api", tags=["SCM"])
v1_router.include_router(orsdm.router, prefix="/api", tags=["ODM"])
v1_router.include_router(sampling.router, prefix="/api", tags=["Odm Sampling Statistics"])
v1_router.include_router(vdcm.router, prefix="/api", tags=["3DCM"])
