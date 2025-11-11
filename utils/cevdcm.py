import json
from datetime import date
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field
import re
from dataclasses import dataclass


class JobType(str, Enum):
    """Enumeration for job types"""
    VIDEO = 'VIDEO'
    IMAGE = 'IMAGE'


class VdcmBase(BaseModel):
    """
    VDCM base model containing common fields
    """
    title: str = Field(None, min_length=1, max_length=255, description="Title of the job")
    sample_number: str = Field(..., min_length=1, max_length=16, description="Sample number identifier")
    sample_batch_number: str = Field(..., min_length=1, max_length=16, description="Batch number for sample group")


class VdcmCreate(VdcmBase):
    """
    VDCM model for creating new jobs
    """
    no: str = Field(None, min_length=1, max_length=32, description="Unique record identifier")
    job_type: JobType = Field(..., description="Type of job - VIDEO or IMAGE")
    src_path: str = Field(..., min_length=1, max_length=255, description="Source path of the media file")
    taken_at: date = Field(None, description="Timestamp when media was captured")
    frame_count: int = Field(default=150, ge=150, description="Number of frames in the media file")


class ErrorLevel(Enum):
    """Error severity levels"""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LogEntry:
    """Log entry data structure"""
    level: ErrorLevel
    message: str
    raw_line: str


class ErrorLogAnalyzer:
    """
    Simplified log analyzer focusing only on error detection
    """

    def __init__(self, error_config_path: str = "patterns.json"):
        self.entries: List[LogEntry] = []
        self.error_config_path = error_config_path
        self.error_patterns = {}

        # Load error patterns
        self._load_error_patterns()

    def _load_error_patterns(self):
        """
        Load error patterns from configuration file
        """
        config_file = Path(self.error_config_path)
        if not config_file.is_absolute():
            config_file = Path(__file__).parent / self.error_config_path

        # Load configuration file
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # Load error patterns
        for name, pattern_config in config.items():
            # Check if the pattern is valid
            self.error_patterns[name] = {
                'pattern': re.compile(pattern_config['pattern'], re.IGNORECASE),
                'message': pattern_config['message'],
                'level': ErrorLevel[pattern_config['level']]
            }

    def parse_log_file(self, log_file_path: str) -> None:
        """
        Parse log file and extract error information
        """
        if not Path(log_file_path).exists():
            return
        # Read log file
        with open(log_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Parse log lines
        for i, line in enumerate(lines, 1):
            entry = self._parse_log_line(line.strip())
            if entry and entry.level in [ErrorLevel.ERROR, ErrorLevel.CRITICAL, ErrorLevel.WARNING]:
                self.entries.append(entry)

    def parse_log_line(self, log_line: str) -> None:
        """
        Parse a single log line string and extract error information
        
        Args:
            log_line (str): A single line from a log file
            
        Returns:
            Optional[LogEntry]: Parsed log entry if it contains error information, None otherwise
        """
        entry = self._parse_log_line(log_line.strip())
        if entry and entry.level in [ErrorLevel.ERROR, ErrorLevel.CRITICAL, ErrorLevel.WARNING]:
            self.entries.append(entry)

    def _parse_log_line(self, line: str) -> Optional[LogEntry]:
        """
        Parse a single log line
        """
        if not line or not line.strip():
            return None

        # Identify stage and error level
        level, message = self._identify_error_level(line)

        return LogEntry(
            level=level,
            message=message,
            raw_line=line
        )

    def _identify_error_level(self, line: str) -> Tuple[ErrorLevel, str]:
        """Identify error level and extract message"""
        # Check predefined error patterns
        for pattern_name, error_info in self.error_patterns.items():
            pattern = error_info['pattern']
            if pattern.search(line):
                # For other patterns, double check that this is actually an error line with explicit error keywords
                # if any(keyword in line.lower() for keyword in ['error', 'failed', 'fatal', 'critical', 'exception']):
                # Skip if this looks like a parameter configuration line
                if re.search(r'^\s*--[\w.]*error|\bRunning\s*:', line, re.IGNORECASE):
                    continue
                return error_info['level'], error_info['message']

        # Check for explicit error keywords
        if re.search(r'^([A-Za-z]*(Error|Exception|Interrupt))', line, re.IGNORECASE):
            return ErrorLevel.CRITICAL, "服务器内部发生异常，请重试或联系管理员"

        return ErrorLevel.INFO, "NOERROR"


if __name__ == '__main__':
    analyzer = ErrorLogAnalyzer("../docker/3dcm/patterns.json")
    analyzer.parse_log_file('app.log')
    print(analyzer.entries)
    