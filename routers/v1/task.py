import logging
import os
import shutil
import zipfile
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, HTTPException, File
from sqlalchemy import desc
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse
from utils.translation import gettext_lazy as _

from models import Job
from models.session import db_manager
from utils.cedoke import (
    get_container_status,
    generate_run_id,
    extract_files_from_zip,
    JobStatus,
    JobRunner,
    move_ownership,
    JobSettings
)
import worker.tasks as tasks

router = APIRouter(prefix='/scm')
UPLOAD_BASE_DIR = Path("static/scm")


def _get_job_or_404(db: Session, job_id: int = None, run_id: str = None) -> Job:
    """Get job by id or run_id, raise 404 if not found."""
    if job_id:
        job = Job.get_by_id(db, job_id)
    elif run_id:
        job = Job.get_by_run_id(db, run_id)
    else:
        raise HTTPException(404, _("Job not found"))
    
    if not job:
        raise HTTPException(404, _("Job not found"))
    
    return job


@router.get(
    "/upload_imgzip_with_usb",
    summary="从 USB 挂载目录导入图片压缩包",
    description=(
        "从 USB 挂载目录读取指定 ZIP 文件，解压图片并以 SSE 流式返回解压进度。\n\n"
        "**支持的图片格式：** jpg, jpeg, png, gif, bmp, webp\n\n"
        "**流式返回格式：**\n"
        "```\n"
        "Progress: 10.00\n"
        "Progress: 20.00\n"
        "...\n"
        "Done: {run_id}, {images_dir}, {file_count}\n"
        "```\n\n"
        "- USB 挂载目录由环境变量 `MOUNT_USB_DIR` 指定\n"
        "- 仅允许 `.zip` 文件"
    ),
    responses={
        200: {"description": "成功建立 SSE 流，持续返回解压进度"},
        400: {"description": "文件格式不支持，仅允许 ZIP 文件"},
    },
)
async def upload_imgzip_with_usb(image: str):
    """
    Upload image zip with usb.
    """
    imgzip = Path(os.getenv("MOUNT_USB_DIR")).joinpath(image.strip("/"))
    if not imgzip.name.endswith(".zip"):
        raise HTTPException(400, "Only ZIP files are allowed")

    run_id = generate_run_id()
    images_dir = UPLOAD_BASE_DIR / run_id
    images_dir.mkdir(parents=True, exist_ok=True)

    def event_generator():
        include_patterns = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
        dst_files = extract_files_from_zip(
            imgzip,
            images_dir.absolute(),
            include_patterns=include_patterns,
            is_copy=False
        )

        file_count = len(dst_files)
        with zipfile.ZipFile(imgzip, 'r') as zip_ref:
            for i, (dst_path, src_path) in enumerate(dst_files, 1):
                with zip_ref.open(src_path) as src, open(dst_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
                    progress = (i / file_count) * 100
                    yield f"Progress: {progress:.2f}\n"
                    logging.info("Extracting %s to %s", src_path.filename, dst_path)

        yield f"Done: {run_id}, {str(images_dir.as_posix())}, {len(dst_files)}"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    )


@router.post(
    "/upload_imgzip_chunked",
    summary="分块上传图片压缩包",
    description=(
        "以分块方式上传 ZIP 文件，上传完成后自动解压，以 SSE 流式返回最终结果。\n\n"
        "**ZIP 文件结构示例：**\n"
        "```\n"
        "myzip.zip\n"
        "├── folder_name/\n"
        "│   ├── 1.jpg\n"
        "│   └── 2.png\n"
        "```\n\n"
        "**支持的图片格式：** jpg, jpeg, png, gif, bmp, webp\n\n"
        "**流式返回格式（上传完成后）：**\n"
        "```\n"
        "{run_id}, {images_dir}, {file_count}\n"
        "```\n\n"
        "- 每次分块大小为 10MB\n"
        "- 仅允许 `.zip` 文件"
    ),
    responses={
        200: {"description": "成功建立 SSE 流，上传完成后返回解压结果"},
        400: {"description": "文件格式不支持，仅允许 ZIP 文件"},
    },
)
async def upload_imgzip_chunked(image: UploadFile = File(...)):
    if not image.filename.endswith(".zip"):
        raise HTTPException(400, "Only ZIP files are allowed")

    imgzip_path = Path("tmp/") / image.filename
    dst_file = move_ownership(image)
    logging.info("move ownership successful, file size: %s", dst_file.size)

    async def event_generator():
        try:
            total_size = 0
            with open(imgzip_path, 'wb') as f:
                while chunk := await dst_file.read(1024 * 1024 * 10):
                    f.write(chunk)
                    total_size += len(chunk)
                    progress = (total_size / dst_file.size) * 100

                    if progress >= 100.00:
                        run_id = generate_run_id()
                        images_dir = UPLOAD_BASE_DIR / run_id

                        if not images_dir.exists():
                            images_dir.mkdir(parents=True)

                        include_patterns = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
                        dst_files = extract_files_from_zip(
                            imgzip_path,
                            images_dir.absolute(),
                            include_patterns=include_patterns
                        )
                        yield f"{run_id}, {str(images_dir.as_posix())}, {len(dst_files)}"
        finally:
            dst_file.file.close()
            imgzip_path.unlink()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    )


@router.post(
    "/upload_img",
    status_code=201,
    summary="上传单张图片",
    description=(
        "上传单张图片到指定任务的图片目录。\n\n"
        "**支持的图片格式：**\n"
        "avif, bmp, dng, heic, heif, jp2, jpeg, jpg, mpo, png, tif, tiff, webp\n\n"
        "**返回示例：**\n"
        "```json\n"
        '{"run_id": "abc123", "image_path": "static/abc123/photo.jpg"}\n'
        "```"
    ),
    responses={
        201: {"description": "图片上传成功，返回 run_id 和图片路径"},
        400: {"description": "不支持的文件格式"},
        500: {"description": "图片保存失败"},
    },
)
async def upload_img(run_id: str, image: UploadFile = File(...)):
    """Upload a single image to the specified job's images directory."""
    allowed_extensions = [
        ".avif", ".bmp", ".dng", ".heic", ".heif",
        ".jp2", ".jpeg", ".jpg", ".mpo", ".png",
        ".tif", ".tiff", ".webp"
    ]
    ext = Path(image.filename).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(400, "Unsupported file format.")

    try:
        images_dir = UPLOAD_BASE_DIR / run_id
        images_dir.mkdir(parents=True, exist_ok=True)

        dst_path = images_dir / image.filename
        contents = await image.read()
        with open(dst_path, 'wb') as f:
            f.write(contents)

        return {"run_id": run_id, "image_path": dst_path.as_posix()}

    except Exception as e:
        logging.error("Failed to upload image: %s", e)
        raise HTTPException(500, "Failed to upload image")

    finally:
        await image.close()


@router.post(
    "/create_job",
    status_code=201,
    summary="创建任务",
    description=(
        "根据任务配置创建一个新任务，不会立即执行。\n\n"
        "图片上传完毕后调用 `job_uploaded` 更新状态，再调用 `start_job` 启动任务。\n\n"
        "**典型工作流：**\n"
        "```\n"
        "create_job → upload_img (多次) → job_uploaded → start_job\n"
        "```"
    ),
    responses={
        201: {"description": "任务创建成功，返回任务详情"},
        422: {"description": "请求参数校验失败"},
    },
)
async def create_job(data: JobSettings, db: Session = Depends(db_manager.get_database)):
    """Create a new job with the given settings."""
    run_id = data.run_id or generate_run_id()
    images_dir = UPLOAD_BASE_DIR / run_id

    return Job.from_settings(data, run_id, images_dir).save(db)


@router.post(
    "/start_job",
    summary="启动任务",
    description=(
        "根据任务 ID 启动一个已创建的任务。\n\n"
        "**可启动的状态：** `waiting` / `failed`\n\n"
        "- `running` / `completed` 状态的任务不可重复启动\n"
        "- 启动成功后状态更新为 `running`"
    ),
    responses={
        200: {"description": "任务启动成功，返回任务详情"},
        400: {"description": "任务当前状态不允许启动"},
        404: {"description": "任务不存在"},
    },
)
async def start_job(job_id: int, db: Session = Depends(db_manager.get_database)):
    """Start a job by ID."""
    job = _get_job_or_404(db=db, job_id=job_id)

    try:
        task = tasks.start_yoscm_task.delay(
            run_id=job.run_id,
            images_dir=str(job.images_dir),
            output_dir=str(job.output_dir),
            log_file=str(job.log_file),
            plant_spacing_cm=job.plant_spacing,
            row_spacing_cm=job.row_spacing,
            ridge_spacing_cm=job.ridge_spacing,
            gsd_cm=job.gsd,
            big_threshold=job.big_threshold,
            small_threshold=job.small_threshold,
        )
        logging.info("Created YOSCM Celery task %s, status %s", task.id, task.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/run_job",
    status_code=201,
    summary="创建并立即运行任务",
    description=(
        "根据任务配置创建任务并立即启动，等同于 `create_job` + `start_job` 的组合。\n\n"
        "适用于图片已提前准备好、无需分步上传的场景。"
    ),
    responses={
        201: {"description": "任务创建并启动成功，返回任务详情"},
        422: {"description": "请求参数校验失败"},
        502: {"description": "容器启动失败"},
    },
)
async def run_job(data: JobSettings, db: Session = Depends(db_manager.get_database)):
    """Create and start a job with the given settings."""
    run_id = data.run_id or generate_run_id()
    images_dir = Path(data.images_dir.strip())

    return Job.from_settings(data, run_id, images_dir).save(db).start(db)


@router.get(
    "/get_job_details",
    summary="获取任务详情",
    description="根据任务 ID 获取任务的完整信息，包含输入参数、运行进度及所有检测图片列表。",
    responses={
        200: {"description": "获取成功，返回任务详情及图片列表"},
        404: {"description": "任务不存在"},
    },
)
async def get_job_details(job_id: int, db: Session = Depends(db_manager.get_database)):
    """Get job details by ID."""
    job = _get_job_or_404(db, job_id=job_id)
    # Lazy load pictures
    db.refresh(job, attribute_names=['pictures'])

    return job


@router.get(
    "/get_job_logs",
    summary="获取任务日志",
    description="根据任务 ID 获取任务运行日志文件的完整内容，以纯文本格式返回。",
    responses={
        200: {"description": "获取成功，返回日志文本"},
        404: {"description": "任务不存在"},
    },
)
async def get_job_logs(job_id: int, db: Session = Depends(db_manager.get_database)):
    """Get job logs by ID."""
    job = _get_job_or_404(db=db, job_id=job_id)

    with open(job.log_file, "r", encoding="utf-8") as f:
        return f.read()


@router.put(
    "/job_uploaded",
    summary="标记任务图片已上传完毕",
    description=(
        "将任务状态更新为 `uploaded`，表示图片已全部上传完毕，可以调用 `start_job` 启动任务。\n\n"
        "**典型工作流：**\n"
        "```\n"
        "create_job → upload_img (多次) → job_uploaded → start_job\n"
        "```"
    ),
    responses={
        200: {"description": "状态更新成功，返回 true"},
        404: {"description": "任务不存在"},
    },
)
async def update_job_status(job_id: int, db: Session = Depends(db_manager.get_database)):
    """Update job status by ID."""
    job = _get_job_or_404(db=db, job_id=job_id)
    job.update_status(db, JobStatus.Uploaded.value)

    return True


@router.get(
    "/get_job_status",
    summary="查询任务运行状态",
    description=(
        "根据任务 ID 实时查询任务的运行状态与进度，会同步检查容器运行状态。\n\n"
        "**状态说明：**\n"
        "- `waiting`：等待执行\n"
        "- `uploaded`：图片已上传，等待启动\n"
        "- `running`：容器正在运行\n"
        "- `completed`：进度 >= 100，任务已完成\n"
        "- `failed`：容器已停止但进度未到 100\n"
    ),
    responses={
        200: {"description": "查询成功，返回状态、进度和 run_id"},
        404: {"description": "任务不存在"},
    },
)
async def get_job_status(job_id: int, db: Session = Depends(db_manager.get_database)) -> JobRunner:
    """Get job status by ID."""
    job = _get_job_or_404(db=db, job_id=job_id)

    if job.container_id:
        is_running = get_container_status(job.container_id)
        sts = JobStatus.Completed if job.progress >= 100.00 else (JobStatus.Running if is_running else JobStatus.Failed)
        job.update_status(db, sts.value)
    else:
        sts = job.status

    return JobRunner(status=sts, progress=job.progress, run_id=job.run_id)


@router.get(
    "/get_jobs",
    summary="获取任务列表",
    description=(
        "分页获取任务列表，支持按状态和任务名称过滤，按创建时间倒序排列。\n\n"
        "**status 可选值（多个用逗号分隔）：**\n"
        "- `waiting`：等待执行\n"
        "- `uploaded`：图片已上传\n"
        "- `running`：正在运行\n"
        "- `completed`：已完成\n"
        "- `failed`：已失败\n\n"
        "**示例：** `status=running,failed`"
    ),
    responses={
        200: {"description": "获取成功，返回任务列表"},
    },
)
async def get_jobs(
        page: int = 1,
        limit: int = 10,
        status: str = None,
        job_name: str = None,
        db: Session = Depends(db_manager.get_database)
):
    """Get a list of jobs with pagination and filtering."""
    query = db.query(Job).order_by(desc(Job.id))
    
    # Filter by status if provided
    if status:
        query = query.filter(Job.status.in_(status.split(",")))

    # Filter by job name if provided
    if job_name:
        query = query.filter(Job.job_name.ilike(f"%{job_name}%"))

    return query.offset((page - 1) * limit).limit(limit).all()


@router.delete(
    "/remove_job_all_res_by_id",
    summary="删除任务及所有关联资源",
    description=(
        "根据任务 ID 删除任务数据库记录，同时删除本地所有关联文件（图片、输出结果、日志等）。\n\n"
        "⚠️ **此操作不可逆，请谨慎调用。**"
    ),
    responses={
        200: {"description": "删除成功，返回 true"},
        404: {"description": "任务不存在"},
    },
)
async def remove_job_all_res_by_id(job_id: int, db: Session = Depends(db_manager.get_database)):
    """Remove job and all associated resources by ID."""
    job = _get_job_or_404(db=db, job_id=job_id)

    job.remove(db)

    return True


@router.get(
    "/update_job_progress",
    summary="更新任务进度（容器回调专用）",
    description=(
        "由容器内部回调，用于上报当前任务的执行进度百分比。\n\n"
        "⚠️ **此接口仅供容器内部调用，请勿在业务侧直接调用。**\n\n"
        "- 进度 `>= 100` 时状态自动更新为 `completed`\n"
        "- 其余情况状态更新为 `running`\n"
        "- 返回值为更新后的进度数值"
    ),
    responses={
        200: {"description": "进度更新成功，返回当前进度值"},
        404: {"description": "任务不存在"},
    },
)
async def update_job_progress(run_id: str, percent: float, db: Session = Depends(db_manager.get_database)):
    """Update job progress by run_id."""
    return Job.update_progress_by_run_id(db, run_id, percent)


@router.post(
    "/save_report",
    summary="保存任务检测报告（容器回调专用）",
    description=(
        "由容器内部回调，用于保存任务的最终检测结果报告。\n\n"
        "⚠️ **此接口仅供容器内部调用，请勿在业务侧直接调用。**\n\n"
        "报告为 JSON 对象，包含检测图片列表及统计数据，保存成功后：\n"
        "- 任务进度自动设为 `100`\n"
        "- 任务状态自动更新为 `completed`\n"
        "- 自动计算健康评分 `score` 和等级 `level`"
    ),
    responses={
        200: {"description": "报告保存成功，返回更新后的任务详情"},
        404: {"description": "任务不存在"},
    },
)
async def save_report(data: dict, db: Session = Depends(db_manager.get_database)):
    """Save job report by run_id."""
    job = _get_job_or_404(db=db, run_id=data.get("run_id"))

    return job.save_report(db, data)
