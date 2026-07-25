"""
Resilient LLM Client for Eco-Loop Building Agents.

This module provides robust communication with the Colab-hosted Ollama endpoint
with comprehensive error handling, timeout mechanisms, and retry logic with
exponential backoff.

Key Features:
- Health check before decision requests
- Automatic retry with exponential backoff
- Comprehensive exception handling
- Consecutive failure tracking for Safety Governor
- Non-blocking timeout mechanisms
"""

import time
from datetime import datetime
from typing import Dict, Any, Optional
import requests
from requests.exceptions import (
    ConnectionError,
    Timeout,
    RequestException,
    HTTPError
)
import json
import urllib3

# Disable SSL warnings for testing endpoints (ngrok/cloudflare tunnels)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from eco_loop_building_agents.models import (
    LLMConfig,
    LLMResponse,
    ZoneState,
    SafetyConfig
)
from eco_loop_building_agents.structured_logger import StructuredLogger


class ResilientLLMClient:
    """
    LLM client with comprehensive fault tolerance.
    
    Provides robust communication with Ollama endpoint including:
    - Connection timeout mechanisms
    - Automatic retry with exponential backoff
    - Health check validation
    - Exception handling for network errors
    - Consecutive failure tracking
    
    Attributes:
        config: LLM configuration parameters
        safety_config: Safety bounds for prompt construction
        logger: Structured logger for events
        consecutive_failures: Count of consecutive failed requests
    """
    
    def __init__(self, config: LLMConfig, logger: StructuredLogger, safety_config: Optional[SafetyConfig] = None):
        """
        Initialize resilient LLM client.
        
        Args:
            config: LLM configuration with endpoint URL, timeouts, retry parameters
            logger: Structured logger for request/response events
            safety_config: Safety bounds configuration for prompt construction (optional)
        """
        self.config = config
        self.logger = logger
        self.safety_config = safety_config or SafetyConfig()
        self._consecutive_failures = 0
        
        self.logger.info(
            component="llm_client",
            event="client_initialized",
            endpoint=config.endpoint_url,
            model=config.model_name,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries
        )
    
    @property
    def consecutive_failures(self) -> int:
        """
        Get count of consecutive failures for Safety Governor integration.
        
        Returns:
            Number of consecutive failed requests
        """
        return self._consecutive_failures
    
    def health_check(self) -> bool:
        """
        Verify LLM endpoint is responsive with short timeout.
        
        Sends a minimal request to the Ollama endpoint to verify availability
        before submitting full control decision requests. Uses a shorter timeout
        than normal requests for fast failure detection.
        
        Returns:
            True if endpoint responds within health_check_timeout, False otherwise
        """
        start_time = time.time()
        
        try:
            # Construct health check endpoint
            # Ollama provides /api/tags endpoint for listing available models
            health_url = f"{self.config.endpoint_url}/api/tags"
            
            self.logger.debug(component="llm_client",
                event="health_check_start",
                url=health_url,
                timeout=self.config.health_check_timeout
            )
            
            # Send GET request with short timeout
            response = requests.get(
                health_url,
                timeout=self.config.health_check_timeout,
                verify=False  # Skip SSL verification for testing endpoints (ngrok/cloudflare tunnels)
            )
            
            # Check for successful response
            response.raise_for_status()
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            self.logger.info(component="llm_client",
                event="health_check_success",
                response_time_ms=round(elapsed_ms, 2)
            )
            
            return True
            
        except Timeout:
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.warning(component="llm_client",
                event="health_check_timeout",
                timeout=self.config.health_check_timeout,
                elapsed_ms=round(elapsed_ms, 2)
            )
            return False
            
        except ConnectionError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.warning(component="llm_client",
                event="health_check_connection_error",
                error=str(e),
                elapsed_ms=round(elapsed_ms, 2)
            )
            return False
            
        except HTTPError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.warning(component="llm_client",
                event="health_check_http_error",
                status_code=e.response.status_code if e.response else None,
                error=str(e),
                elapsed_ms=round(elapsed_ms, 2)
            )
            return False
            
        except Exception as e:
            # Catch-all for unexpected errors
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.error(component="llm_client",
                event="health_check_unexpected_error",
                error_type=type(e).__name__,
                error=str(e),
                elapsed_ms=round(elapsed_ms, 2)
            )
            return False
    
    def request_control_decision(
        self,
        zone_states: Dict[str, ZoneState],
        energy_metrics: Dict[str, float],
        simulation_time: datetime
    ) -> LLMResponse:
        """
        Request control decision from LLM with retry logic.
        
        Constructs a context-rich prompt with building state, sends to LLM endpoint,
        and parses the response. Implements exponential backoff retry on failures.
        
        Args:
            zone_states: Current state of all zones (temperature, humidity, PMV, etc.)
            energy_metrics: Cumulative energy consumption (hvac_kwh, lighting_kwh, etc.)
            simulation_time: Current simulation timestamp
            
        Returns:
            LLMResponse with decision data or error details
        """
        # Construct prompt with building context
        prompt = self._construct_prompt(zone_states, energy_metrics, simulation_time)
        
        self.logger.info(component="llm_client",
            event="decision_request_start",
            simulation_time=simulation_time.isoformat(),
            zone_count=len(zone_states),
            prompt_length=len(prompt)
        )
        
        # Execute request with retry logic
        response = self._execute_with_retry(prompt)
        
        # Update consecutive failure tracking
        if response.success:
            self._consecutive_failures = 0
            self.logger.info(component="llm_client",
                event="decision_request_success",
                response_time_ms=response.response_time_ms,
                consecutive_failures_reset=True
            )
        else:
            self._consecutive_failures += 1
            self.logger.error(component="llm_client",
                event="decision_request_failed",
                error=response.error_message,
                consecutive_failures=self._consecutive_failures
            )
        
        return response
    
    def _execute_with_retry(self, prompt: str) -> LLMResponse:
        """
        Execute LLM request with exponential backoff retry.
        
        Attempts the request up to max_retries times, waiting progressively
        longer between attempts using exponential backoff:
        - Attempt 1: immediate
        - Attempt 2: wait backoff_base^1 seconds
        - Attempt 3: wait backoff_base^2 seconds
        - etc.
        
        Args:
            prompt: Formatted prompt string for LLM
            
        Returns:
            LLMResponse with decision or error details
        """
        last_error = None
        
        for attempt in range(self.config.max_retries):
            # Log retry attempt
            if attempt > 0:
                self.logger.info(component="llm_client",
                    event="retry_attempt",
                    attempt=attempt + 1,
                    max_retries=self.config.max_retries
                )
            
            try:
                # Execute single request attempt
                response = self._execute_single_request(prompt)
                
                # If successful, return immediately
                if response.success:
                    return response
                
                # If unsuccessful but no exception, store error for potential retry
                last_error = response.error_message
                
            except Exception as e:
                # Unexpected exception during request
                last_error = f"Unexpected error: {type(e).__name__}: {str(e)}"
                self.logger.error(component="llm_client",
                    event="request_exception",
                    attempt=attempt + 1,
                    error_type=type(e).__name__,
                    error=str(e)
                )
            
            # If not the last attempt, apply exponential backoff
            if attempt < self.config.max_retries - 1:
                wait_time = self.config.backoff_base ** (attempt + 1)
                
                self.logger.info(component="llm_client",
                    event="exponential_backoff",
                    wait_seconds=wait_time,
                    next_attempt=attempt + 2
                )
                
                time.sleep(wait_time)
        
        # All retries exhausted
        self.logger.error(component="llm_client",
            event="all_retries_exhausted",
            max_retries=self.config.max_retries,
            final_error=last_error
        )
        
        return LLMResponse(
            success=False,
            decision=None,
            error_message=f"All {self.config.max_retries} retry attempts failed. Last error: {last_error}",
            response_time_ms=0.0
        )
    
    def _execute_single_request(self, prompt: str) -> LLMResponse:
        """
        Execute a single LLM request without retry logic.
        
        Sends prompt to Ollama endpoint, handles various failure modes,
        and parses response into structured decision format.
        
        Args:
            prompt: Formatted prompt string for LLM
            
        Returns:
            LLMResponse with decision or error details
        """
        start_time = time.time()
        
        try:
            # Construct Ollama generate endpoint
            generate_url = f"{self.config.endpoint_url}/api/generate"
            
            # Prepare request payload
            payload = {
                "model": self.config.model_name,
                "prompt": prompt,
                "stream": False  # Get complete response, not streaming
            }
            
            self.logger.debug(component="llm_client",
                event="http_request",
                url=generate_url,
                model=self.config.model_name,
                timeout=self.config.timeout_seconds
            )
            
            # Send POST request with timeout
            response = requests.post(
                generate_url,
                json=payload,
                timeout=self.config.timeout_seconds,
                verify=False  # Skip SSL verification for testing endpoints (ngrok/cloudflare tunnels)
            )
            
            # Check for HTTP errors (4xx, 5xx)
            response.raise_for_status()
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Parse response JSON
            try:
                response_json = response.json()
            except json.JSONDecodeError as e:
                self.logger.error(component="llm_client",
                    event="malformed_response",
                    error="Failed to parse response JSON",
                    response_text=response.text[:500]  # Log first 500 chars
                )
                return LLMResponse(
                    success=False,
                    decision=None,
                    error_message=f"Malformed JSON response: {str(e)}",
                    response_time_ms=round(elapsed_ms, 2)
                )
            
            # Extract generated text from Ollama response format
            if "response" not in response_json:
                self.logger.error(component="llm_client",
                    event="invalid_response_format",
                    error="Missing 'response' field in Ollama response",
                    response_keys=list(response_json.keys())
                )
                return LLMResponse(
                    success=False,
                    decision=None,
                    error_message="Invalid response format: missing 'response' field",
                    response_time_ms=round(elapsed_ms, 2)
                )
            
            llm_text = response_json["response"]
            
            # Parse decision from LLM text
            decision = self._parse_decision(llm_text)
            
            if decision is None:
                return LLMResponse(
                    success=False,
                    decision=None,
                    error_message="Failed to parse control decision from LLM response",
                    response_time_ms=round(elapsed_ms, 2)
                )
            
            self.logger.info(component="llm_client",
                event="request_success",
                response_time_ms=round(elapsed_ms, 2),
                decision_zone_count=len(decision)
            )
            
            return LLMResponse(
                success=True,
                decision=decision,
                error_message=None,
                response_time_ms=round(elapsed_ms, 2)
            )
            
        except Timeout:
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.error(component="llm_client",
                event="request_timeout",
                timeout=self.config.timeout_seconds,
                elapsed_ms=round(elapsed_ms, 2)
            )
            return LLMResponse(
                success=False,
                decision=None,
                error_message=f"Request timeout after {self.config.timeout_seconds}s",
                response_time_ms=round(elapsed_ms, 2)
            )
            
        except ConnectionError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.error(component="llm_client",
                event="connection_error",
                error=str(e),
                elapsed_ms=round(elapsed_ms, 2)
            )
            return LLMResponse(
                success=False,
                decision=None,
                error_message=f"Connection error: {str(e)}",
                response_time_ms=round(elapsed_ms, 2)
            )
            
        except HTTPError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            status_code = e.response.status_code if e.response else None
            self.logger.error(component="llm_client",
                event="http_error",
                status_code=status_code,
                error=str(e),
                elapsed_ms=round(elapsed_ms, 2)
            )
            return LLMResponse(
                success=False,
                decision=None,
                error_message=f"HTTP error {status_code}: {str(e)}",
                response_time_ms=round(elapsed_ms, 2)
            )
            
        except RequestException as e:
            # Catch-all for other requests library exceptions
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.error(component="llm_client",
                event="request_exception",
                error_type=type(e).__name__,
                error=str(e),
                elapsed_ms=round(elapsed_ms, 2)
            )
            return LLMResponse(
                success=False,
                decision=None,
                error_message=f"Request error: {str(e)}",
                response_time_ms=round(elapsed_ms, 2)
            )
            
        except Exception as e:
            # Catch-all for unexpected errors
            elapsed_ms = (time.time() - start_time) * 1000
            self.logger.error(component="llm_client",
                event="unexpected_error",
                error_type=type(e).__name__,
                error=str(e),
                elapsed_ms=round(elapsed_ms, 2)
            )
            return LLMResponse(
                success=False,
                decision=None,
                error_message=f"Unexpected error: {type(e).__name__}: {str(e)}",
                response_time_ms=round(elapsed_ms, 2)
            )
    
    def _construct_prompt(
        self,
        zone_states: Dict[str, ZoneState],
        energy_metrics: Dict[str, float],
        simulation_time: datetime
    ) -> str:
        """
        Build context-rich prompt for LLM.
        
        Constructs a detailed prompt including:
        - Current zone temperatures and comfort metrics
        - Cumulative energy consumption
        - Time of day and occupancy information
        - Control objective (minimize energy, maintain comfort)
        
        Args:
            zone_states: Current state of all zones
            energy_metrics: Cumulative energy metrics
            simulation_time: Current simulation timestamp
            
        Returns:
            Formatted prompt string
        """
        prompt_parts = []
        
        # System context
        prompt_parts.append(
            "You are an AI building energy controller managing HVAC and lighting systems. "
            "Your goal is to minimize energy consumption while maintaining thermal comfort "
            "within ASHRAE 55 standards (PMV between -0.5 and +0.5).\n"
        )
        
        # Current time
        prompt_parts.append(
            f"\nCurrent Time: {simulation_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        # Zone states
        prompt_parts.append("\nCurrent Zone States:")
        for zone_id, state in zone_states.items():
            prompt_parts.append(
                f"  {zone_id}:"
                f" Temperature={state.temperature:.1f}°C,"
                f" Humidity={state.humidity:.2f},"
                f" Occupancy={state.occupancy},"
                f" PMV={state.pmv:.2f}"
            )
        
        # Energy metrics
        prompt_parts.append("\nCumulative Energy Consumption:")
        for metric_name, value in energy_metrics.items():
            prompt_parts.append(f"  {metric_name}: {value:.2f} kWh")
        
        # Control request
        prompt_parts.append(
            "\n\nProvide control decisions for each zone in JSON format:\n"
            "{\n"
            '  "zone_id": {"heating_setpoint": 20.0, "cooling_setpoint": 24.0, "lighting_fraction": 0.8},\n'
            '  ...\n'
            "}\n"
            "\nEnsure heating_setpoint < cooling_setpoint and all values are within safety bounds:\n"
            f"- Heating: {self.safety_config.min_heating_setpoint}-{self.safety_config.max_heating_setpoint}°C\n"
            f"- Cooling: {self.safety_config.min_cooling_setpoint}-{self.safety_config.max_cooling_setpoint}°C\n"
            "- Lighting: 0.0-1.0"
        )
        
        return "\n".join(prompt_parts)
    
    def _parse_decision(self, llm_text: str) -> Optional[Dict[str, Any]]:
        """
        Parse control decision from LLM response text.
        
        Attempts to extract JSON-formatted control decisions from LLM output.
        Handles cases where LLM includes explanatory text around JSON.
        
        Args:
            llm_text: Raw text response from LLM
            
        Returns:
            Dictionary mapping zone IDs to control parameters, or None if parsing fails
        """
        try:
            # Try to find JSON object in response
            # Look for content between first { and last }
            start_idx = llm_text.find('{')
            end_idx = llm_text.rfind('}')
            
            if start_idx == -1 or end_idx == -1:
                self.logger.warning(component="llm_client",
                    event="decision_parse_failed",
                    error="No JSON object found in response",
                    response_preview=llm_text[:200]
                )
                return None
            
            json_text = llm_text[start_idx:end_idx + 1]
            decision = json.loads(json_text)
            
            # Validate decision structure
            if not isinstance(decision, dict):
                self.logger.warning(component="llm_client",
                    event="decision_parse_failed",
                    error="Decision is not a dictionary",
                    decision_type=type(decision).__name__
                )
                return None
            
            # Validate each zone has required fields
            for zone_id, zone_decision in decision.items():
                if not isinstance(zone_decision, dict):
                    self.logger.warning(component="llm_client",
                        event="decision_parse_failed",
                        error=f"Zone {zone_id} decision is not a dictionary"
                    )
                    return None
                
                required_fields = ["heating_setpoint", "cooling_setpoint", "lighting_fraction"]
                for field in required_fields:
                    if field not in zone_decision:
                        self.logger.warning(component="llm_client",
                            event="decision_parse_failed",
                            error=f"Zone {zone_id} missing required field: {field}"
                        )
                        return None
            
            self.logger.debug(component="llm_client",
                event="decision_parsed",
                zone_count=len(decision)
            )
            
            return decision
            
        except json.JSONDecodeError as e:
            self.logger.warning(component="llm_client",
                event="decision_json_decode_error",
                error=str(e),
                json_text=json_text[:200] if 'json_text' in locals() else None
            )
            return None
            
        except Exception as e:
            self.logger.error(component="llm_client",
                event="decision_parse_unexpected_error",
                error_type=type(e).__name__,
                error=str(e)
            )
            return None
