# DCMG - Drone Crop Monitoring Gateway

A lightweight FastAPI service to manage Docker containers and process agricultural drone imagery for crop monitoring.

No frontend (API-only), easy to extend.

Exposes REST APIs to interact with Docker containers and OpenDroneMap (ODM) processing tasks.

## Directory
```plaintext
dcmg/
├── alembic/                     # Database migrations
│   ├── versions/                # Migration scripts
├── models/                      # Database models
│   ├── __init__.py
│   └── session.py               # Database session management
├── routers/                     # API endpoints
│   ├── __init__.py
│   ├── api.py                   # Main API router
│   └── v1/                      # API version 1
├── utils/                       # Utility functions
│   ├── __init__.py
├── worker/                      # Celery worker
│   ├── app.py                   # Celery app config
│   └── tasks/                   # Task implementations
│       ├── __init__.py
├── static/                      # Static files
├── tmp/                         # Temporary files
├── main.py                      # Application entry
├── requirements.txt             # Dependencies
├── .env.example                 # Env vars template
└── README.md                    # Documentation
```

## Features

1. **Docker Container Management**:
   - Start/stop Docker containers for image processing tasks
   - Monitor container status and logs
   - Manage container lifecycle

2. **Drone Image Processing Pipeline**:
   - Process agricultural drone imagery using OpenDroneMap (ODM)
   - Handle image copying and preprocessing tasks
   - Track processing progress and status

3. **Crop Status Monitoring (CSM)**:
   - Deep learning-based plant growth monitoring system
   - Analyzes plant images to provide real-time crop status evaluation
   - Calculates statistical metrics for crop health assessment

4. **Task Management**:
   - Asynchronous task processing with Celery
   - Database-backed job tracking and persistence
   - Real-time progress updates

5. **ODM Report Management**:
   - Automated ODM report generation and processing
   - Progress tracking for report uploads to OSS (Object Storage Service)
   - Report commit to online systems with metadata
   - Task cancellation support for report uploads
   - Enhanced error handling and state management


## Requirements

- Docker
- Python 3.9+
- Redis (for Celery broker/result backend)
- FastAPI
- Docker SDK for Python
- SQLAlchemy
- Celery
- OpenDroneMap server

## Technology Stack

- **Web Framework**: FastAPI (Python)
- **Database**: SQLAlchemy with Alembic migrations
- **Asynchronous Task Queue**: Celery with Redis backend
- **Containerization**: Docker Engine API
- **Message Broker**: Redis
- **Image Processing**: OpenDroneMap (ODM)
- **Serialization**: Pydantic models
- **ORM**: SQLAlchemy
- **Database Migrations**: Alembic


## Installation
```bash
# Clone the repository
git clone https://github.com/glock-fei/dcmg.git
cd dcmg

# Create static and tmp directories (if needed)
mkdir static tmp

# Create a virtual environment
python -m venv venv 

# Activate the virtual environment
source venv/bin/activate # Linux/Mac
# or
venv\Scripts\activate # Windows

# install requirements
pip install -r requirements.txt
````

## Redis Setup
```bash
# Pull Redis image
docker pull redis:latest
# Run Redis container
docker run --name my-redis -d -p 6379:6379 --restart unless-stopped redis:latest 
```

## Start celery worker
```bash
# Start the Celery worker
celery -A worker.app worker --loglevel=info
````
## Configuration

### Set environment variables by copying the example file
```bash
# Edit .env file with your configuration
copy .env.example .env
``` 

## Database Setup
```bash
# Create database migrations (if needed)
# alembic revision --autogenerate -m "init"

# Upgrade the database to the latest versio
alembic upgrade head
```

## Run the Application

The project supports internationalization using Babel. To work with translations:
```bash
python main.py
# Or with uvicorn:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
## Internationalization
```shell
# Extract translatable messages
pybabel extract -F babel.cfg -o messages.pot .

# Initialize a new language (e.g., Chinese)
pybabel init -i messages.pot -d locales -l zh_CN

# Update existing translations
pybabel update -i messages.pot -d locales -l zh_CN

# Compile translations
pybabel compile -d locales
```

# CSM - Crop Status Monitoring
## Project Overview
CSM is a deep learning-based plant growth monitoring system that analyzes plant images to provide real-time crop status evaluation.

## Statistical Calculation Methods

| Field Name              | Type  | Formula/Calculation Method                         | Parameter Description              | Example Value                  | Explanation                              |
|-------------------------|-------|----------------------------------------------------|------------------------------------|--------------------------------|------------------------------------------|
| **`CM_TO_M`**           | float | 0.01                                               | Centimeter to meter ratio          | 0.01                           | Unit conversion base                     |
| **`MU_TO_SQ_M`**        | float | 10000/15                                           | 1 mu = 666.67 sq.meters            | 666.67                         | Area unit conversion                     |
| **`GSD`**               | float | Input parameter                                    | Ground sample distance (cm/pixel)  | 0.32                           | Related to drone altitude                |
| **`big_threshold`**     | float | Input parameter                                    | Large plant threshold (area ratio) | 0.2                            | Criteria for large/medium plants         |
| **`small_threshold`**   | float | Input parameter                                    | Small plant threshold (area ratio) | 0.3                            | Criteria for medium/small plants         |
| **`row_spacing_cm`**    | float | Input parameter                                    | Row spacing (cm)                   | 80.0                           | Distance between rows                    |
| **`plant_spacing_cm`**  | float | Input parameter                                    | Plant spacing (cm)                 | 10.0                           | Distance between plants                  |
| **`ridge_width_cm`**    | float | Input parameter (optional)                         | Ridge width (cm)                   | None                           | Some fields have ridges                  |
| **`area_mu`**           | float | [Reference](## area_mu)                            | 0.2725                             | Area per image (mu)            |
| **`num_of_picture_mu`** | int   | [Reference](## num_of_picture_mu)                  | 2271                               | Theoretical max plant capacity |
| **`num_big`**           | int   | &gt; [avg_area](## avg_area) * (1 + big_threshold) | Detected large plants              | 625                            | Plants meeting large criteria            |
| **`num_medium`**        | int   | ~ (num_big &#124; num_small)                       | Detected medium plants             | 673                            | Plants meeting medium criteria           |
| **`num_small`**         | int   | &lt; avg_area * (1 - small_threshold)              | Detected small plants              | 431                            | Plants meeting small criteria            |
| **`num_total`**         | int   | num_big + num_medium + num_small                   | Total detected plants              | 1729                           | Total plants in image                    |
| **`num_of_per_mu`**     | int   | (1/area_mu) × num_total                            | Plants per mu (actual)             | 6343                           | Actual planting density                  |
| **`big_ratio`**         | float | (num_big / num_of_picture_mu) × 100                | Percentage calculation             | 27.52                          | Large plant ratio (vs capacity)          |
| **`medium_ratio`**      | float | (num_medium / num_of_picture_mu) × 100             | Percentage calculation             | 29.63                          | Medium plant ratio (vs capacity)         |
| **`small_ratio`**       | float | (num_small / num_of_picture_mu) × 100              | Percentage calculation             | 18.98                          | Small plant ratio (vs capacity)          |
| **`total_ratio`**       | float | (num_total / num_of_picture_mu) × 100              | Percentage calculation             | 76.13                          | Total coverage ratio (vs capacity)       |
| **`vaild_ratio`**       | float | big_ratio + medium_ratio                           | Percentage sum                     | 57.16                          | Valid plants ratio (production standard) |

### num_of_picture_mu
> Definition: Theoretical maximum plant capacity for a single image's coverage area under current planting parameters (row/plant spacing, ridge width)
> Core purpose: Serves as benchmark for detection results to calculate large/medium/small plant ratios
#### Parameter description:
- row_spacing_m = row_spacing_cm × CM_TO_M
- plant_spacing_m = plant_spacing_cm × CM_TO_M
- ridge_width_m = ridge_width_cm × CM_TO_M (when ridge_width_cm > 0)
#### Calculation formula:
- **Without ridges**: math.floor(area_mu×MU_TO_SQ_M×(1/(row_spacing_m×plant_spacing_m)))
- **With ridges**: math.floor(area_mu×MU_TO_SQ_M×(1/plant_spacing_m)×(1/(row_spacing_m+ridge_width_m)))

### area_mu
> Definition: Converts image pixel dimensions to actual planting area (mu)
> Core purpose: Serves as benchmark for detection results to estimate plant count (outputs num_of_picture_mu)
#### Calculation formula:
- area_mu = (width × height × GSD² × CM_TO_M²) / MU_TO_SQ_M
#### Parameter description:
- width/height: Image pixel dimensions
- GSD: Ground sample distance (cm/pixel)
- CM_TO_M² = 0.01² = 0.0001

### avg_area
> Definition: Mean area of all detection boxes in the field (using Z-score normalization)
> Core purpose: Serves as benchmark for detection results to calculate large/medium/small plant counts
> Uses z-score standardization for all detection box areas to obtain mean value
#### Calculation logic:
- Collect pixel areas of all detection boxes
- Calculate Z-score and remove outliers (typically points where |Z|>3)
- Calculate arithmetic mean of remaining data
