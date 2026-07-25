"""
Fault Injection Test Harness for Eco-Loop Building Agents.

This module provides deliberate failure injection to validate system resilience
and recovery mechanisms. It intercepts LLM client requests and injects various
fault types based on configuration.

Key Features:
- Timeout simulation (blocking for configurable duration)
- Connection refusal simulation
- Malformed response injection
- Extreme control decision injection
- Configuration-based control (enable/disable, fault type, rate)
- Decorator pattern integration with LLMClient

Fault Types:
- timeout: Simulate LLM request timeout by blocking
- connection_error: Simulate endpoint unavailability
- malformed_json: Return invalid JSON response
- extreme_values: Inject out-of-bounds control decisions
"""

import time
import random
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from functools import wraps

from eco_loop_building_agents.models import (
    FaultConfig,
    LLMResponse,
    ZoneState
)
from eco_loop_building_agents.structured_logger import StructuredLogger


class FaultInjector:
    """
    Intercepts LLM requests to inject faults for resilience testing.
    
    The fault injector wraps LLM client methods and conditionally introduces
    failures based on configuration. Faults can be triggered probabilistically
    (based on fault_rate) or for sustained durations (based on fault_duration_seconds).
    
    Attributes:
        config: Fault injection configuration
        logger: Structured logger for fault events
        _fault_start_time: Timestamp when sustained fault period began (None if inactive)
        _total_requests: Total number of intercepted requests
        _injected_faults: Count of injected faults
    """
    
    def __init__(self, config: FaultConfig, logger: StructuredLogger):
        """
        Initialize fault injector.
        
        Args:
            config: Fault injection configuration with type, rate, and duration
            logger: Structured logger for fault injection events
        """
        self.config = config
        self.logger = logger
        self._fault_start_time: Optional[float] = None
        self._total_requests = 0
        self._injected_faults = 0
        
        if self.config.enabled:
            self.logger.info(
                component="fault_injector",
                event="injector_initialized",
                fault_type=config.fault_type,
                fault_rate=config.fault_rate,
                fault_duration=config.fault_duration_seconds
            )
        else:
            self.logger.info(
                component="fault_injector",
                event="injector_disabled"
            )
    
    def should_inject_fault(self) -> bool:
        """
        Determine if a fault should be injected for the current request.
        
        Decision logic:
        1. If fault injection is disabled, return False
        2. If within sustained fault period, return True
        3. Otherwise, use probabilistic decision based on fault_rate
        
        Returns:
            True if fault should be injected, False otherwise
        """
        if not self.config.enabled:
            return False
        
        self._total_requests += 1
        
        # Check if within sustained fault period
        if self._fault_start_time is not None:
            elapsed = time.time() - self._fault_start_time
            if elapsed < self.config.fault_duration_seconds:
                # Still within fault period
                return True
            else:
                # Fault period ended
                self.logger.info(
                    component="fault_injector",
                    event="fault_period_ended",
                    duration=elapsed,
                    faults_injected=self._injected_faults
                )
                self._fault_start_time = None
        
        # Probabilistic fault injection based on fault_rate
        if random.random() < self.config.fault_rate:
            # Start new fault period
            self._fault_start_time = time.time()
            self.logger.info(
                component="fault_injector",
                event="fault_period_started",
                fault_type=self.config.fault_type,
                duration=self.config.fault_duration_seconds
            )
            return True
        
        return False
    
    def inject_timeout_fault(self) -> LLMResponse:
        """
        Simulate LLM request timeout by blocking.
        
        Blocks for a duration exceeding normal timeout to trigger timeout handling
        in the calling code.
        
        Returns:
            LLMResponse indicating timeout failure
        """
        self._injected_faults += 1
        
        # Block for duration exceeding normal timeout
        block_duration = 35.0  # Exceeds typical 30s timeout
        
        self.logger.warning(
            component="fault_injector",
            event="timeout_fault_injected",
            block_duration=block_duration,
            request_number=self._total_requests
        )
        
        time.sleep(block_duration)
        
        return LLMResponse(
            success=False,
            decision=None,
            error_message="INJECTED FAULT: Request timeout",
            response_time_ms=block_duration * 1000
        )
    
    def inject_connection_error_fault(self) -> LLMResponse:
        """
        Simulate endpoint unavailability / connection refusal.
        
        Returns an LLM response indicating connection failure without
        actually attempting network communication.
        
        Returns:
            LLMResponse indicating connection error
        """
        self._injected_faults += 1
        
        self.logger.warning(
            component="fault_injector",
            event="connection_error_fault_injected",
            request_number=self._total_requests
        )
        
        return LLMResponse(
            success=False,
            decision=None,
            error_message="INJECTED FAULT: Connection refused - endpoint unavailable",
            response_time_ms=0.0
        )
    
    def inject_malformed_json_fault(self) -> LLMResponse:
        """
        Simulate malformed JSON response from LLM.
        
        Returns a response that appears successful but contains unparseable
        decision data, triggering JSON parsing error handling.
        
        Returns:
            LLMResponse with malformed decision structure
        """
        self._injected_faults += 1
        
        self.logger.warning(
            component="fault_injector",
            event="malformed_json_fault_injected",
            request_number=self._total_requests
        )
        
        # Return response with invalid decision structure
        # This will trigger parsing failures in the LLM client
        return LLMResponse(
            success=False,
            decision=None,
            error_message="INJECTED FAULT: Malformed JSON response - invalid decision structure",
            response_time_ms=100.0
        )
    
    def inject_extreme_values_fault(self, zone_states: Dict[str, ZoneState]) -> LLMResponse:
        """
        Inject out-of-bounds control decisions to test Safety Governor.
        
        Generates decisions with extreme setpoint values that violate safety bounds,
        testing the Safety Governor's clamping and validation logic.
        
        Args:
            zone_states: Current zone states to generate decisions for
            
        Returns:
            LLMResponse with extreme control decisions
        """
        self._injected_faults += 1
        
        self.logger.warning(
            component="fault_injector",
            event="extreme_values_fault_injected",
            request_number=self._total_requests,
            zone_count=len(zone_states)
        )
        
        # Generate extreme decisions for each zone
        extreme_decisions = {}
        for zone_id in zone_states.keys():
            # Alternate between too-hot and too-cold scenarios
            if hash(zone_id) % 2 == 0:
                # Too hot: very low setpoints
                extreme_decisions[zone_id] = {
                    "heating_setpoint": 10.0,  # Way below minimum (18°C)
                    "cooling_setpoint": 12.0,
                    "lighting_fraction": 0.0
                }
            else:
                # Too cold: very high setpoints
                extreme_decisions[zone_id] = {
                    "heating_setpoint": 30.0,  # Way above maximum (22°C)
                    "cooling_setpoint": 35.0,  # Way above maximum (28°C)
                    "lighting_fraction": 1.0
                }
        
        return LLMResponse(
            success=True,  # Appears successful to test Safety Governor validation
            decision=extreme_decisions,
            error_message=None,
            response_time_ms=100.0
        )
    
    def intercept_request(
        self,
        original_method: Callable,
        zone_states: Dict[str, ZoneState],
        energy_metrics: Dict[str, float],
        simulation_time: datetime
    ) -> LLMResponse:
        """
        Intercept LLM request and conditionally inject faults.
        
        This is the main interception point. It decides whether to inject a fault
        or allow the original request to proceed normally.
        
        Args:
            original_method: Original request_control_decision method
            zone_states: Current zone states
            energy_metrics: Energy consumption metrics
            simulation_time: Current simulation timestamp
            
        Returns:
            LLMResponse from either fault injection or original method
        """
        # Check if fault should be injected
        if not self.should_inject_fault():
            # No fault - proceed with original request
            return original_method(zone_states, energy_metrics, simulation_time)
        
        # Inject fault based on configured type
        if self.config.fault_type == "timeout":
            return self.inject_timeout_fault()
        
        elif self.config.fault_type == "connection_error":
            return self.inject_connection_error_fault()
        
        elif self.config.fault_type == "malformed_json":
            return self.inject_malformed_json_fault()
        
        elif self.config.fault_type == "extreme_values":
            return self.inject_extreme_values_fault(zone_states)
        
        else:
            # Unknown fault type - log error and proceed normally
            self.logger.error(
                component="fault_injector",
                event="unknown_fault_type",
                fault_type=self.config.fault_type
            )
            return original_method(zone_states, energy_metrics, simulation_time)
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get fault injection statistics.
        
        Returns:
            Dictionary with total requests, injected faults, and injection rate
        """
        injection_rate = self._injected_faults / self._total_requests if self._total_requests > 0 else 0.0
        
        return {
            "total_requests": self._total_requests,
            "injected_faults": self._injected_faults,
            "injection_rate": injection_rate,
            "fault_type": self.config.fault_type,
            "configured_rate": self.config.fault_rate
        }


def with_fault_injection(fault_injector: FaultInjector):
    """
    Decorator to wrap LLM client methods with fault injection.
    
    This decorator intercepts calls to request_control_decision and routes them
    through the fault injector for conditional fault injection.
    
    Usage:
        @with_fault_injection(fault_injector)
        def request_control_decision(self, zone_states, energy_metrics, simulation_time):
            # Original implementation
            ...
    
    Args:
        fault_injector: FaultInjector instance to use for interception
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, zone_states: Dict[str, ZoneState], 
                   energy_metrics: Dict[str, float], 
                   simulation_time: datetime) -> LLMResponse:
            # Route through fault injector
            return fault_injector.intercept_request(
                lambda zs, em, st: func(self, zs, em, st),
                zone_states,
                energy_metrics,
                simulation_time
            )
        return wrapper
    return decorator


class FaultInjectionWrapper:
    """
    Wrapper class that applies fault injection to an existing LLM client.
    
    This provides an alternative to the decorator pattern - wrapping an existing
    LLM client instance to add fault injection behavior without modifying the
    original class.
    
    Usage:
        original_client = ResilientLLMClient(config, logger)
        wrapped_client = FaultInjectionWrapper(original_client, fault_config, logger)
        
        # Use wrapped client - faults will be injected automatically
        response = wrapped_client.request_control_decision(zones, metrics, time)
    """
    
    def __init__(self, llm_client, fault_config: FaultConfig, logger: StructuredLogger):
        """
        Initialize fault injection wrapper.
        
        Args:
            llm_client: Original LLM client instance to wrap
            fault_config: Fault injection configuration
            logger: Structured logger for fault events
        """
        self._client = llm_client
        self._injector = FaultInjector(fault_config, logger)
        self.logger = logger
        
        # Forward config and consecutive_failures from wrapped client
        self.config = llm_client.config
        
    @property
    def consecutive_failures(self) -> int:
        """Forward consecutive_failures from wrapped client."""
        return self._client.consecutive_failures
    
    def health_check(self) -> bool:
        """
        Forward health check to wrapped client without fault injection.
        
        Health checks are not intercepted for fault injection to allow
        accurate system health monitoring.
        
        Returns:
            Result from wrapped client's health check
        """
        return self._client.health_check()
    
    def request_control_decision(
        self,
        zone_states: Dict[str, ZoneState],
        energy_metrics: Dict[str, float],
        simulation_time: datetime
    ) -> LLMResponse:
        """
        Request control decision with fault injection.
        
        Routes through fault injector which may inject faults or proceed
        with normal request.
        
        Args:
            zone_states: Current zone states
            energy_metrics: Energy consumption metrics
            simulation_time: Current simulation timestamp
            
        Returns:
            LLMResponse from either fault injection or wrapped client
        """
        return self._injector.intercept_request(
            self._client.request_control_decision,
            zone_states,
            energy_metrics,
            simulation_time
        )
    
    def get_fault_statistics(self) -> Dict[str, Any]:
        """
        Get fault injection statistics.
        
        Returns:
            Dictionary with injection statistics
        """
        return self._injector.get_statistics()
