#!/bin/bash

INPUT_DIR=""
OUTPUT_DIR=""
LICENSE=""
ALGOS=""
PRIVATE_KEY="FshGzcaQQliP7eXU"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input_dir) INPUT_DIR="$2"; shift ;;
        --output_dir) OUTPUT_DIR="$2"; shift ;;
        --license) LICENSE="$2"; shift ;;
        --private_key) PRIVATE_KEY="$2"; shift ;;
        --algos) ALGOS="$2"; shift ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

if [[ -z "$INPUT_DIR" || -z "$OUTPUT_DIR" || -z "$LICENSE" || -z "$PRIVATE_KEY" ]]; then
    echo "Usage: $0 --input_dir DIR --output_dir DIR --license FILE --private_key KEY"
    exit 1
fi

# Enhanced debug info with better formatting
echo "=========================================="
echo "           RSDM Configuration             "
echo "=========================================="
echo "  Input Directory : $INPUT_DIR"
echo "  File Count      : $(ls "$INPUT_DIR" 2>/dev/null | wc -l) files"
echo "  Output Directory: $OUTPUT_DIR"
echo "  License File    : $LICENSE"
echo "  Private Key     : ${PRIVATE_KEY:0:5}*****${PRIVATE_KEY: -5}"
echo "=========================================="
echo ""

docker run --rm \
  -v "$INPUT_DIR:/media" \
  -v "$OUTPUT_DIR:/app/images/output" \
  -v "$LICENSE:/app/license" \
  -v "$ALGOS:/app/algos.json" \
  -e "RSDM_PRIVATE_KEY=$PRIVATE_KEY" \
  registry.cn-shenzhen.aliyuncs.com/glock/rsdm:0.0.1-dev \
  /bin/bash -c "cp -r /media/* /app/images/ && ./start"
