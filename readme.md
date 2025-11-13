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

## Docker Deployment

The application can be deployed using Docker Compose which includes all necessary services:

1. **Build and Start Services**:
```bash
# Build the Docker images
docker-compose build --no-cache

# Start all services in detached mode
docker-compose up -d
```

2. **Check Service Status**:
```bash
# View running containers
docker-compose ps

# View logs for a specific service
docker-compose logs -f app
docker-compose logs -f redis
docker-compose logs -f worker-default
```

3. **Stop Services**:
```bash
# Stop all services
docker-compose down

# Stop services and remove volumes
docker-compose down -v
```

4. **Service Descriptions**:
   - `app`: Main FastAPI application service, exposed on port 7777
   - `redis`: Redis service for Celery message broker and result backend
   - `worker-default`: Celery worker for default task queue
   - `worker-generate-odm`: Celery worker for ODM report generation tasks
   - `worker-upload-odm`: Celery worker for ODM report upload tasks
   - `worker-reconstruction`: Celery worker for reconstruction tasks

## Manual Installation
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
```

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
```

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

# Upgrade the database to the latest version
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