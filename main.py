import logging
import os

import uvicorn
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from routers.api import v1_router
from worker.recover_tasks import recover_job_failed

# API tags metadata
TAGS_METADATA = [
    {
        "name": "SCM",
        "description": (
            "**作物状态监测 (Crop Status Monitoring)**\n\n"
            "基于深度学习的植物生长监测系统，通过无人机影像分析提供实时作物状态评估。\n"
            "支持图片上传、任务管理、容器化检测执行及结果统计。\n\n"
            "- 检测算法：深度学习目标检测\n"
            "- 输出指标：植株数量、大小分级、健康评分、等级评定\n"
            "- 支持格式：jpg, jpeg, png, gif, bmp, webp, tif, tiff 等\n"
            "- 执行方式：Docker 容器异步执行"
        ),
    },
    {
        "name": "ODM",
        "description": (
            "**开放无人机地图处理 (OpenDroneMap Processing)**\n\n"
            "管理 ODM 任务的生命周期，包括影像上传、正射影像生成、植被指数计算、报告生成及云端上传。\n"
            "支持多光谱和 RGB 影像处理，自动生成植被分析报告。\n\n"
            "- 数据源：无人机多光谱/RGB 影像\n"
            "- 处理类型：正射校正、植被指数提取（NDVI、NDRE 等）\n"
            "- 输出产物：正射影像图、植被指数图、分析报告\n"
            "- 存储支持：本地文件系统 + OSS 对象存储\n"
            "- 异步处理：Celery 多队列任务调度"
        ),
    },
    {
        "name": "Odm Sampling Statistics",
        "description": (
            "**ODM 样方统计分析**\n\n"
            "基于 ODM 生成的正射影像和植被指数图，对指定样方区域进行统计分析。\n"
            "支持自定义样方坐标、批量统计计算、结果导出 Excel。\n\n"
            "- 分析维度：样方内植被指数均值、标准差、最值等\n"
            "- 样方类型：正方形、圆形（基于 GPS 坐标）\n"
            "- 统计指标：按植被类型分类统计\n"
            "- 数据导出：Excel 格式，支持流式下载"
        ),
    },
    {
        "name": "CropPheno",
        "description": (
            "**作物表型重建 (Crop Phenotype Reconstruction)**\n\n"
            "通过视频或图像序列重建作物三维表型，生成点云模型并提取表型参数。\n"
            "支持表型报告上传至云端系统。\n\n"
            "- 输入格式：视频文件（MP4 等）或图像序列\n"
            "- 重建方式：三维重建算法（点云生成）\n"
            "- 输出产物：PLY 点云文件、覆盖图、表型报告\n"
            "- 帧数要求：默认 150 帧\n"
            "- 云端集成：支持上传至表型管理平台"
        ),
    },
]

# API description
API_DESCRIPTION = """
# DCMG - Drone Crop Monitoring Gateway API

无人机作物监测网关服务，提供农业无人机影像处理、作物状态监测、表型重建的 RESTful API 接口。

## 核心功能模块

### 1. SCM - 作物状态监测
基于深度学习的植株检测与生长评估系统：
- 图片上传（单张/压缩包/USB 挂载/分块上传）
- 任务创建与执行（Docker 容器化运行）
- 实时进度监控与日志查看
- 检测结果统计（数量、大小分级、健康评分）

### 2. ODM - 开放无人机地图处理
完整的 ODM 影像处理流水线管理：
- 任务创建与影像自动扫描
- 背景任务复制影像至 ODM 服务器
- 正射影像与植被指数图生成
- 分析报告生成与保存
- 报告上传至 OSS 及业务系统

### 3. Odm Sampling Statistics - 样方统计分析
基于 ODM 产物的精细化样方分析：
- 样方记录管理（创建/查询/删除）
- 基于 GPS 坐标的样方划定
- 植被指数统计分析（后台 Celery 任务）
- Excel 数据导出

### 4. CropPheno - 作物表型重建
三维表型重建与报告管理：
- 视频/图像序列表型重建任务
- 点云模型生成（PLY 格式）
- 任务进度监控与取消
- 表型报告上传至云端

## 技术架构

- **Web 框架**: FastAPI (Python)
- **数据库**: SQLAlchemy + Alembic (SQLite/PostgreSQL)
- **异步任务**: Celery + Redis (多队列调度)
- **容器化**: Docker Engine API (SCM 检测任务)
- **影像处理**: OpenDroneMap (ODM)
- **对象存储**: 阿里云 OSS

## 工作流程

### SCM 检测流程
```
上传图片 → create_job → upload_img (多次) → job_uploaded → start_job → 容器执行 → save_report (回调)
```

### ODM 处理流程
```
create_odm_job → 后台复制影像 → ODM 处理 → generate_report → save_report → upload_report (上传 OSS)
```

### CropPheno 重建流程
```
create_croppheno_job → 后台重建任务 → 生成点云 → upload_report (上传云端)
```

## 公共说明

- **任务状态**: `pending` → `running` → `completed` / `failed` / `canceled`
- **分页参数**: `page` (页码，默认 1), `limit` (每页数量，默认 10/1000)
- **异步任务**: 大部分耗时操作通过 Celery 后台执行，返回 Celery Task ID
- **文件存储**: 静态文件存储在 `static/` 目录，可通过 `/static` 路径访问

## 状态码

- `200 OK` — 请求成功
- `201 Created` — 资源创建成功
- `202 Accepted` — 请求已接受，后台处理中
- `204 No Content` — 操作成功，无返回内容
- `400 Bad Request` — 请求参数错误或业务逻辑校验失败
- `404 Not Found` — 资源不存在
- `422 Unprocessable Entity` — 请求参数校验失败
- `500 Internal Server Error` — 服务器内部错误
- `502 Bad Gateway` — 外部服务调用失败
"""

# create app
app = FastAPI(
    title="DCMG",
    version="0.1.0",
    description=API_DESCRIPTION,
    lifespan=recover_job_failed,
    openapi_tags=TAGS_METADATA
)

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
        log_level=logging.INFO,
        env_file=os.path.join(os.getcwd(), ".env")
    )
