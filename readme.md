# DCMG
A lightweight FastAPI service to manage Docker containers.

No frontend (API-only), easy to extend.

Exposes REST APIs to interact with Docker (start/stop containers).

## Requirements
- Docker
- Python 3.9+
- FastAPI
- Docker SDK for Python

## Installation
```bash
# clone the repository
git clone https://github.com/glock-fei/dcmg.git
cd dcmg

# create static, tmp directories
mkdir static
mkdir tmp

# create a virtual environment
python -m venv venv 

# activate the virtual environment  
source venv/bin/activate

# install requirements
pip install -r requirements.txt
````

## Set environment variables
```bash
# create a .env file in the root directory
touch .env

# add the following environment variables to the .env file
SERVICE_PORT = 7700
SERVICE_HOST_GATEWAY = "dcmg.host.server"
STATIC_DIR = "static"
SCM_IMAGE = "registry.cn-shenzhen.aliyuncs.com/glock/yoscm:0.0.2-det"
SCM_PRIVATE_KEY = "8Qp6DNRfKaR1h!jv"
PROGRESS_URL = "http://${SCM_HOST_GATEWAY}:${SCM_SERVICE_PORT}/api/scm/update_job_progress"
REPORT_URL = "http://${SCM_HOST_GATEWAY}:${SCM_SERVICE_PORT}/api/scm/save_report"

# OSS configuration
OSS_ACCESS_KEY_ID = ""
OSS_ACCESS_KEY_SECRET = ""
OSS_BUCKET = ""
OSS_ENDPOINT = ""
OSS_REGION = ""
OSS_DOAMIN = "" # oss domain
OSS_UPLOAD_KEY = "" # oss upload key
``` 
## Set up database
```bash
# create a new migration
# alembic revision --autogenerate -m "init"

# upgrade the database
alembic upgrade head
```

## Run the app
```bash

python main.py
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
