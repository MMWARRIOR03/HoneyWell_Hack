"""
Core data models for Eco-Loop Building Agents system.

This module defines the fundamental dataclasses used throughout the system
for representing zone state, control decisions, and configuration parameters.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


@dataclass
class ZoneState:
    """
    Represents the current state of a thermal zone in the building.
    
    Attributes:
        zone_id: Unique identifier for the thermal zone
        temperature: Current zone temperature in °C
        humidity: Relative humidity as a fraction (0-1)
        occupancy: Number of occupants currently in the zone
        pmv: Predicted Mean Vote thermal comfort metric (-3 to +3, target -0.5 to +0.5)
        timestamp: When this state was measured
    """
    zone_id: str
    temperature: float  # °C
    humidity: float  # Relative humidity (0-1)
    occupancy: int  # Number of occupants
    pmv: float  # Predicted Mean Vote
    timestamp: datetime
    
    def __post_init__(self):
        """Validate zone state values."""
        if not 0 <= self.humidity <= 1:
            raise ValueError(f"Humidity must be between 0 and 1, got {self.humidity}")
        if self.occupancy < 0:
            raise ValueError(f"Occupancy cannot be negative, got {self.occupancy}")


@dataclass
class ControlDecision:
    """
    Represents an HVAC and lighting control decision for a zone.
    
    Attributes:
        zone_id: Unique identifier for the thermal zone
        heating_setpoint: Target heating setpoint in °C
        cooling_setpoint: Target cooling setpoint in °C
        lighting_fraction: Lighting level as a fraction (0-1, where 1 is full brightness)
        timestamp: When this decision was made
        source: Origin of the decision ("ai" or "fallback")
    """
    zone_id: str
    heating_setpoint: float  # °C
    cooling_setpoint: float  # °C
    lighting_fraction: float  # 0-1
    timestamp: datetime
    source: str  # "ai" or "fallback"
    
    def __post_init__(self):
        """Validate control decision values."""
        if not 0 <= self.lighting_fraction <= 1:
            raise ValueError(f"Lighting fraction must be between 0 and 1, got {self.lighting_fraction}")
        if self.heating_setpoint >= self.cooling_setpoint:
            raise ValueError(
                f"Heating setpoint ({self.heating_setpoint}) must be less than "
                f"cooling setpoint ({self.cooling_setpoint})"
            )
        if self.source not in ("ai", "fallback"):
            raise ValueError(f"Source must be 'ai' or 'fallback', got {self.source}")


@dataclass
class SafetyConfig:
    """
    Configuration for safety bounds on HVAC control.
    
    Defines the operational limits for heating and cooling setpoints to ensure
    occupant comfort and prevent unsafe or inefficient control decisions.
    
    Attributes:
        min_heating_setpoint: Minimum allowed heating setpoint in °C
        max_heating_setpoint: Maximum allowed heating setpoint in °C
        min_cooling_setpoint: Minimum allowed cooling setpoint in °C
        max_cooling_setpoint: Maximum allowed cooling setpoint in °C
        min_deadband: Minimum gap between heating and cooling setpoints in °C
        pmv_min: Minimum acceptable PMV value per ASHRAE 55
        pmv_max: Maximum acceptable PMV value per ASHRAE 55
    """
    min_heating_setpoint: float = 18.0  # °C
    max_heating_setpoint: float = 22.0  # °C
    min_cooling_setpoint: float = 22.0  # °C
    max_cooling_setpoint: float = 28.0  # °C
    min_deadband: float = 2.0  # Minimum cooling - heating gap in °C
    pmv_min: float = -0.5  # ASHRAE 55 comfort band minimum
    pmv_max: float = 0.5  # ASHRAE 55 comfort band maximum
    
    def __post_init__(self):
        """Validate safety configuration values."""
        if self.min_heating_setpoint >= self.max_heating_setpoint:
            raise ValueError("min_heating_setpoint must be less than max_heating_setpoint")
        if self.min_cooling_setpoint >= self.max_cooling_setpoint:
            raise ValueError("min_cooling_setpoint must be less than max_cooling_setpoint")
        if self.max_heating_setpoint > self.min_cooling_setpoint:
            raise ValueError("max_heating_setpoint must not exceed min_cooling_setpoint")
        if self.min_deadband < 0:
            raise ValueError("min_deadband must be non-negative")
        if self.pmv_min >= self.pmv_max:
            raise ValueError("pmv_min must be less than pmv_max")


@dataclass
class LLMConfig:
    """
    Configuration for LLM client connection and behavior.
    
    Defines connection parameters, timeout settings, and retry logic for
    communicating with the Ollama endpoint hosting the Qwen2.5-7B-Instruct model.
    
    Attributes:
        endpoint_url: Base URL for the Ollama API endpoint
        model_name: Name of the model to use for inference
        timeout_seconds: Maximum time to wait for a response before timeout
        max_retries: Maximum number of retry attempts for failed requests
        backoff_base: Base for exponential backoff calculation (wait = base^attempt)
        health_check_timeout: Timeout for health check requests in seconds
    """
    endpoint_url: str = "http://localhost:11434"
    model_name: str = "qwen2.5:7b-instruct"
    timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_base: float = 2.0
    health_check_timeout: float = 5.0
    
    def __post_init__(self):
        """Validate LLM configuration values."""
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.backoff_base <= 1:
            raise ValueError("backoff_base must be greater than 1")
        if self.health_check_timeout <= 0:
            raise ValueError("health_check_timeout must be positive")
        if not self.endpoint_url:
            raise ValueError("endpoint_url cannot be empty")
        if not self.model_name:
            raise ValueError("model_name cannot be empty")


class SystemHealthState(Enum):
    """
    Represents the current health state of the AI control system.
    
    Values:
        HEALTHY: AI control is working normally
        DEGRADED: AI control is experiencing issues but retrying
        FALLBACK: Rule-based control is active due to AI failures
    """
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FALLBACK = "fallback"


@dataclass
class LLMResponse:
    """
    Response from an LLM decision request.
    
    Attributes:
        success: Whether the request completed successfully
        decision: Parsed decision data if successful (dict with zone decisions)
        error_message: Error description if failed
        response_time_ms: Time taken for the request in milliseconds
    """
    success: bool
    decision: Optional[dict] = None
    error_message: Optional[str] = None
    response_time_ms: float = 0.0
    
    def __post_init__(self):
        """Validate LLM response consistency."""
        if self.success and self.decision is None:
            raise ValueError("Successful response must include decision data")
        if not self.success and self.error_message is None:
            raise ValueError("Failed response must include error message")


@dataclass
class FaultConfig:
    """
    Configuration for fault injection testing.
    
    Enables deliberate introduction of failures to validate system resilience.
    
    Attributes:
        enabled: Whether fault injection is active
        fault_type: Type of fault to inject (timeout, connection_error, malformed_json, extreme_values)
        fault_rate: Probability of fault per request (0-1)
        fault_duration_seconds: Duration for sustained faults
    """
    enabled: bool = False
    fault_type: str = "timeout"
    fault_rate: float = 0.1
    fault_duration_seconds: float = 60.0
    
    def __post_init__(self):
        """Validate fault configuration values."""
        valid_types = ["timeout", "connection_error", "malformed_json", "extreme_values"]
        if self.fault_type not in valid_types:
            raise ValueError(f"fault_type must be one of {valid_types}, got {self.fault_type}")
        if not 0 <= self.fault_rate <= 1:
            raise ValueError(f"fault_rate must be between 0 and 1, got {self.fault_rate}")
        if self.fault_duration_seconds < 0:
            raise ValueError("fault_duration_seconds must be non-negative")


@dataclass
class SimulationConfig:
    """
    Configuration for EnergyPlus simulation parameters.
    
    Attributes:
        idf_path: Path to the IDF building model file
        epw_path: Path to the EPW weather file
        decision_interval_hours: Hours between control decision cycles
    """
    idf_path: str = "./models/baseline.idf"
    epw_path: str = "./weather/IND_New.Delhi.432950_ISHRAE.epw"
    decision_interval_hours: int = 1
    
    def __post_init__(self):
        """Validate simulation configuration values."""
        if self.decision_interval_hours <= 0:
            raise ValueError("decision_interval_hours must be positive")


@dataclass
class LoggingConfig:
    """
    Configuration for structured logging.
    
    Attributes:
        log_dir: Directory for log file output
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Whether to use JSON-lines format
    """
    log_dir: str = "./logs"
    log_level: str = "INFO"
    json_format: bool = True
    
    def __post_init__(self):
        """Validate logging configuration values."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}, got {self.log_level}")


@dataclass
class SystemConfig:
    """
    Complete system configuration.
    
    Aggregates all configuration sections into a single object.
    
    Attributes:
        llm: LLM client configuration
        safety: Safety bounds configuration
        simulation: Simulation parameters
        logging: Logging configuration
        fault_injection: Fault injection configuration (optional)
    """
    llm: LLMConfig
    safety: SafetyConfig
    simulation: SimulationConfig
    logging: LoggingConfig
    fault_injection: Optional[FaultConfig] = None
