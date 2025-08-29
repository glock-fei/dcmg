import logging
import os
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from routers.api import v1_router
from worker.recover_tasks import recover_job_failed

# load env variables
load_dotenv(dotenv_path=".env")

# create app
app = FastAPI(title="DCMG", lifespan=recover_job_failed)

# mount static files
app.mount("/static", StaticFiles(directory=os.getenv("STATIC_DIR", "static")), name="images")
# cors middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# include routers
app.include_router(v1_router)

# run app
if __name__ == '__main__':
    uvicorn.run(
        app,
        host='0.0.0.0',
        port=int(os.getenv('SERVICE_PORT', 7777)),
        log_config='logs/conf.ini',
        log_level=logging.INFO
    )
