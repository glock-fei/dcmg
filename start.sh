#!/bin/bash
set -euo pipefail

# Configuration
PORT="${1:-7700}"
BASE_DIR="${BASE_DIR:-~/dcmg}"
LOG_DIR="${LOG_DIR:-${BASE_DIR}/logs/celery}"
PID_DIR="${PID_DIR:-${BASE_DIR}/pids}"

# Create directories
mkdir -p "$LOG_DIR" "$PID_DIR"

# Logging setup
CURRENT_DATE=$(date +%Y%m%d)
MAIN_LOG="${LOG_DIR}/celery_${CURRENT_DATE}.log"
ERROR_LOG="${LOG_DIR}/celery_error_${CURRENT_DATE}.log"

# Activate conda environment
source ~/anaconda3/bin/activate dcmg

cd "$BASE_DIR"
# Clean old PID files
find "$PID_DIR" -name "*.pid" -mtime +1 -delete 2>/dev/null || true

# Function to check if Redis is available
wait_for_redis() {
    local max_attempts=30
    local attempt=1
    local redis_host="${REDIS_HOST:-localhost}"
    local redis_port="${REDIS_PORT:-6379}"

    echo "$(date '+%Y-%m-%d %H:%M:%S') - Checking Redis connectivity on $redis_host:$redis_port..." >> "$MAIN_LOG"

    while [ $attempt -le $max_attempts ]; do
        if nc -z "$redis_host" "$redis_port" 2>/dev/null; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Redis is available on $redis_host:$redis_port" >> "$MAIN_LOG"
            return 0
        fi

        echo "$(date '+%Y-%m-%d %H:%M:%S') - Waiting for Redis ($attempt/$max_attempts)..." >> "$MAIN_LOG"
        sleep 2
        attempt=$((attempt + 1))
    done

    echo "$(date '+%Y-%m-%d %H:%M:%S') - ERROR: Redis is not available on $redis_host:$redis_port after $max_attempts attempts" >> "$ERROR_LOG"
    return 1
}

# Wait for Redis to be available
if ! wait_for_redis; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ERROR: Cannot start application without Redis" >> "$ERROR_LOG"
    exit 1
fi

# Start Celery workers
echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting Celery workers..." >> "$MAIN_LOG"

# Start workers in background
celery -A worker.app worker -l info -Q generate_odm_report -n geworker@%h -c 1 \
    --pidfile="${PID_DIR}/geworker.pid" >> "$MAIN_LOG" 2>> "$ERROR_LOG" &
GEWORKER_PID=$!

celery -A worker.app worker -l info -Q upload_odm_report -n upworker@%h -c 1 \
    --pidfile="${PID_DIR}/upworker.pid" >> "$MAIN_LOG" 2>> "$ERROR_LOG" &
UPWORKER_PID=$!

celery -A worker.app worker -l info -Q default -n cpworker@%h -c 1 \
    --pidfile="${PID_DIR}/cpworker.pid" >> "$MAIN_LOG" 2>> "$ERROR_LOG" &
CPWORKER_PID=$!

celery -A worker.app worker -l info -Q reconstruction -n reconworker@%h -c 1 \
    --pidfile="${PID_DIR}/reconworker.pid" >> "$MAIN_LOG" 2>> "$ERROR_LOG" &
REWORKER_PID=$!

# Store PIDs for cleanup
echo $GEWORKER_PID > "${PID_DIR}/geworker.pid"
echo $UPWORKER_PID > "${PID_DIR}/upworker.pid"
echo $CPWORKER_PID > "${PID_DIR}/cpworker.pid"
echo $REWORKER_PID > "${PID_DIR}/reconworker.pid"

# Wait a moment for workers to start
sleep 5

# Check if workers are running
WORKERS_RUNNING=true
for pid in $GEWORKER_PID $UPWORKER_PID $CPWORKER_PID; do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ERROR: Worker with PID $pid failed to start" >> "$ERROR_LOG"
        WORKERS_RUNNING=false
    fi
done

if [ "$WORKERS_RUNNING" = false ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ERROR: Some workers failed to start" >> "$ERROR_LOG"
    exit 1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') - All Celery workers started successfully" >> "$MAIN_LOG"

# Cleanup function
cleanup() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Shutting down..." >> "$MAIN_LOG"

    # Kill worker processes
    for pid in $GEWORKER_PID $UPWORKER_PID $CPWORKER_PID; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait a bit for graceful shutdown
    sleep 3

    # Force kill if still running
    for pid in $GEWORKER_PID $UPWORKER_PID $CPWORKER_PID; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done

    # Remove PID files
    rm -f "$PID_DIR"/*.pid
}

# Set up signal traps
trap cleanup EXIT TERM INT

# Start Uvicorn
echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting Uvicorn on port $PORT" >> "$MAIN_LOG"
exec uvicorn main:app --port "$PORT" --host 0.0.0.0 --log-config logs/conf.ini --log-level info
