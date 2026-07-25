"""
Structured Logging for comprehensive, machine-parseable event logging.

This module provides the StructuredLogger class for JSON-lines format logging
that enables detailed debugging of multi-day simulation runs. All log events
are written as single-line JSON objects with timestamps and structured data.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List
from enum import Enum

from eco_loop_building_agents.models import ZoneState, ControlDecision


class LogLevel(Enum):
    """Log level enumeration matching standard logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class StructuredLogger:
    """
    Thread-safe structured logger for JSON-lines format logging.
    
    The StructuredLogger writes all events as single-line JSON objects to enable
    machine parsing and analysis of simulation runs. Each log entry includes:
    - timestamp: ISO 8601 formatted timestamp
    - level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - component: Source component (orchestration_loop, llm_client, etc.)
    - event: Event type (decision_cycle_start, llm_response, etc.)
    - Additional event-specific fields
    
    Key Features:
    - Timestamp-based log file creation (run_YYYY-MM-DDTHH-MM-SS.jsonl)
    - Thread-safe writes for concurrent logging from multiple components
    - Specialized methods for logging decision cycles, exceptions, and PMV violations
    - Automatic directory creation if log directory doesn't exist
    
    Attributes:
        log_dir: Directory path for log file output
        log_level: Minimum log level to record
        log_file_path: Full path to current log file
        _lock: Thread lock for synchronized writes
        _file_handle: Open file handle for log file
    """
    
    def __init__(self, log_dir: str, log_level: str = "INFO"):
        """
        Initialize the structured logger with timestamp-based log file.
        
        Creates a new log file with timestamp in filename format:
        run_YYYY-MM-DDTHH-MM-SS.jsonl
        
        Args:
            log_dir: Directory path for log file output
            log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            
        Raises:
            ValueError: If log_level is not a valid level
            OSError: If log directory cannot be created
        """
        # Validate log level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}, got {log_level}")
        
        self.log_dir = Path(log_dir)
        self.log_level = LogLevel[log_level]
        self._lock = threading.Lock()
        
        # Create log directory if it doesn't exist
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create timestamp-based log filename
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        log_filename = f"run_{timestamp}.jsonl"
        self.log_file_path = self.log_dir / log_filename
        
        # Open log file in append mode with line buffering
        self._file_handle = open(self.log_file_path, 'a', buffering=1)
    
    def _should_log(self, level: LogLevel) -> bool:
        """
        Check if a message at the given level should be logged.
        
        Args:
            level: Log level to check
            
        Returns:
            True if level is >= configured minimum level
        """
        level_order = {
            LogLevel.DEBUG: 0,
            LogLevel.INFO: 1,
            LogLevel.WARNING: 2,
            LogLevel.ERROR: 3,
            LogLevel.CRITICAL: 4
        }
        return level_order[level] >= level_order[self.log_level]
    
    def _write_log_entry(
        self,
        level: LogLevel,
        component: str,
        event: str,
        **kwargs: Any
    ) -> None:
        """
        Write a log entry as a single-line JSON object.
        
        Thread-safe method that writes structured log data to the log file.
        Each entry is a complete JSON object on a single line (JSON-lines format).
        
        Args:
            level: Log level for this entry
            component: Source component name
            event: Event type identifier
            **kwargs: Additional event-specific fields
            
        Thread Safety:
            Protected by threading.Lock to ensure atomic writes from
            multiple threads.
        """
        if not self._should_log(level):
            return
        
        # Build log entry dictionary
        log_entry = {
            "timestamp": datetime.now().isoformat() + "Z",
            "level": level.value,
            "component": component,
            "event": event
        }
        
        # Add all additional fields
        log_entry.update(kwargs)
        
        # Write as single-line JSON
        with self._lock:
            json_line = json.dumps(log_entry, default=str)
            self._file_handle.write(json_line + "\n")
    
    def log_decision_cycle_start(
        self,
        simulation_time: datetime,
        zone_states: Dict[str, ZoneState]
    ) -> None:
        """
        Log the start of a control decision cycle.
        
        Args:
            simulation_time: Current simulation timestamp
            zone_states: Current state of all zones
        """
        zone_data = {}
        for zone_id, state in zone_states.items():
            zone_data[zone_id] = {
                "temperature": state.temperature,
                "humidity": state.humidity,
                "occupancy": state.occupancy,
                "pmv": state.pmv,
                "timestamp": state.timestamp.isoformat()
            }
        
        self._write_log_entry(
            LogLevel.INFO,
            "orchestration_loop",
            "decision_cycle_start",
            simulation_time=simulation_time.isoformat(),
            zone_states=zone_data
        )
    
    def log_llm_request(
        self,
        prompt_length: int,
        timeout: float,
        zone_count: int
    ) -> None:
        """
        Log an LLM decision request.
        
        Args:
            prompt_length: Length of prompt in characters
            timeout: Request timeout in seconds
            zone_count: Number of zones in request
        """
        self._write_log_entry(
            LogLevel.INFO,
            "llm_client",
            "llm_request",
            prompt_length=prompt_length,
            timeout=timeout,
            zone_count=zone_count
        )
    
    def log_llm_response(
        self,
        success: bool,
        response_time_ms: float,
        error_message: Optional[str] = None
    ) -> None:
        """
        Log an LLM response.
        
        Args:
            success: Whether the request succeeded
            response_time_ms: Response time in milliseconds
            error_message: Error description if failed
        """
        log_data = {
            "success": success,
            "response_time_ms": response_time_ms
        }
        
        if error_message:
            log_data["error_message"] = error_message
        
        level = LogLevel.INFO if success else LogLevel.ERROR
        
        self._write_log_entry(
            level,
            "llm_client",
            "llm_response",
            **log_data
        )
    
    def log_decision_validated(
        self,
        decision: ControlDecision,
        modified: bool
    ) -> None:
        """
        Log a validated control decision.
        
        Args:
            decision: Validated control decision
            modified: Whether safety governor modified the decision
        """
        self._write_log_entry(
            LogLevel.INFO if not modified else LogLevel.WARNING,
            "safety_governor",
            "decision_validated",
            zone=decision.zone_id,
            heating_setpoint=decision.heating_setpoint,
            cooling_setpoint=decision.cooling_setpoint,
            lighting_fraction=decision.lighting_fraction,
            source=decision.source,
            modified=modified,
            timestamp=decision.timestamp.isoformat()
        )
    
    def log_pmv_violation(
        self,
        zone_id: str,
        pmv: float,
        timestamp: datetime
    ) -> None:
        """
        Log a PMV comfort violation outside ASHRAE 55 band.
        
        Args:
            zone_id: Zone identifier
            pmv: PMV value outside acceptable range
            timestamp: When violation occurred
        """
        self._write_log_entry(
            LogLevel.WARNING,
            "safety_governor",
            "pmv_violation",
            zone=zone_id,
            pmv=pmv,
            violation_time=timestamp.isoformat()
        )
    
    def log_fallback_activation(
        self,
        trigger_reason: str,
        consecutive_failures: int
    ) -> None:
        """
        Log Safety Governor fallback activation.
        
        Args:
            trigger_reason: Why fallback was activated
            consecutive_failures: Number of consecutive LLM failures
        """
        self._write_log_entry(
            LogLevel.WARNING,
            "safety_governor",
            "fallback_activated",
            trigger_reason=trigger_reason,
            consecutive_failures=consecutive_failures
        )
    
    def log_fallback_deactivation(self) -> None:
        """Log restoration of AI-driven control after fallback."""
        self._write_log_entry(
            LogLevel.INFO,
            "safety_governor",
            "fallback_deactivated"
        )
    
    def log_exception(
        self,
        component: str,
        exception_type: str,
        message: str,
        stack_trace: str,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log an exception with full details.
        
        Args:
            component: Component where exception occurred
            exception_type: Exception class name
            message: Exception message
            stack_trace: Full stack trace string
            context: Additional context information
        """
        log_data = {
            "exception_type": exception_type,
            "message": message,
            "stack_trace": stack_trace
        }
        
        if context:
            log_data["context"] = context
        
        self._write_log_entry(
            LogLevel.ERROR,
            component,
            "exception",
            **log_data
        )
    
    def log_health_check(
        self,
        success: bool,
        response_time_ms: Optional[float] = None,
        error_message: Optional[str] = None
    ) -> None:
        """
        Log LLM health check result.
        
        Args:
            success: Whether health check passed
            response_time_ms: Response time if successful
            error_message: Error description if failed
        """
        log_data = {"success": success}
        
        if response_time_ms is not None:
            log_data["response_time_ms"] = response_time_ms
        
        if error_message:
            log_data["error_message"] = error_message
        
        level = LogLevel.INFO if success else LogLevel.WARNING
        
        self._write_log_entry(
            level,
            "llm_client",
            "health_check",
            **log_data
        )
    
    def log_energy_metrics(
        self,
        simulation_time: datetime,
        hvac_energy_kwh: float,
        lighting_energy_kwh: float,
        total_energy_kwh: float
    ) -> None:
        """
        Log cumulative energy consumption metrics.
        
        Args:
            simulation_time: Current simulation timestamp
            hvac_energy_kwh: HVAC energy consumption in kWh
            lighting_energy_kwh: Lighting energy consumption in kWh
            total_energy_kwh: Total energy consumption in kWh
        """
        self._write_log_entry(
            LogLevel.INFO,
            "orchestration_loop",
            "energy_metrics",
            simulation_time=simulation_time.isoformat(),
            hvac_energy_kwh=hvac_energy_kwh,
            lighting_energy_kwh=lighting_energy_kwh,
            total_energy_kwh=total_energy_kwh
        )
    
    def log_simulation_start(
        self,
        idf_path: str,
        epw_path: str,
        decision_interval_hours: int
    ) -> None:
        """
        Log simulation start with configuration.
        
        Args:
            idf_path: Path to IDF building model file
            epw_path: Path to EPW weather file
            decision_interval_hours: Hours between decision cycles
        """
        self._write_log_entry(
            LogLevel.INFO,
            "orchestration_loop",
            "simulation_start",
            idf_path=idf_path,
            epw_path=epw_path,
            decision_interval_hours=decision_interval_hours
        )
    
    def log_simulation_end(
        self,
        total_duration_seconds: float,
        decision_cycles_completed: int
    ) -> None:
        """
        Log simulation completion.
        
        Args:
            total_duration_seconds: Total simulation runtime
            decision_cycles_completed: Number of decision cycles executed
        """
        self._write_log_entry(
            LogLevel.INFO,
            "orchestration_loop",
            "simulation_end",
            total_duration_seconds=total_duration_seconds,
            decision_cycles_completed=decision_cycles_completed
        )
    
    def debug(self, component: str, event: str, **kwargs: Any) -> None:
        """
        Log a debug-level message.
        
        Args:
            component: Source component name
            event: Event type
            **kwargs: Additional fields
        """
        self._write_log_entry(LogLevel.DEBUG, component, event, **kwargs)
    
    def info(self, component: str, event: str, **kwargs: Any) -> None:
        """
        Log an info-level message.
        
        Args:
            component: Source component name
            event: Event type
            **kwargs: Additional fields
        """
        self._write_log_entry(LogLevel.INFO, component, event, **kwargs)
    
    def warning(self, component: str, event: str, **kwargs: Any) -> None:
        """
        Log a warning-level message.
        
        Args:
            component: Source component name
            event: Event type
            **kwargs: Additional fields
        """
        self._write_log_entry(LogLevel.WARNING, component, event, **kwargs)
    
    def error(self, component: str, event: str, **kwargs: Any) -> None:
        """
        Log an error-level message.
        
        Args:
            component: Source component name
            event: Event type
            **kwargs: Additional fields
        """
        self._write_log_entry(LogLevel.ERROR, component, event, **kwargs)
    
    def critical(self, component: str, event: str, **kwargs: Any) -> None:
        """
        Log a critical-level message.
        
        Args:
            component: Source component name
            event: Event type
            **kwargs: Additional fields
        """
        self._write_log_entry(LogLevel.CRITICAL, component, event, **kwargs)
    
    def close(self) -> None:
        """
        Close the log file handle.
        
        Should be called when logging is complete to ensure all data is flushed.
        """
        with self._lock:
            if self._file_handle and not self._file_handle.closed:
                self._file_handle.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures file is closed."""
        self.close()
        return False
