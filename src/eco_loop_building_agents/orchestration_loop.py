"""
Orchestration Loop for coordinating hourly control decision cycles.

This module implements the OrchestrationLoop class which serves as the main
control coordinator for the Eco-Loop Building Agents system. It manages the
simulation lifecycle, coordinates hourly decision-making cycles, and integrates
all system components into a cohesive control system.

Key Responsibilities:
- Load system configuration from config.yaml
- Initialize all components (DecisionCache, LLMClient, SafetyGovernor, etc.)
- Manage hourly decision cycles during simulation
- Construct context-rich prompts for LLM with building state
- Coordinate data flow: read zones → request LLM → validate → write decisions
- Maintain comprehensive structured logging
"""

import time
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path

from .decision_cache import DecisionCache
from .llm_client import ResilientLLMClient
from .baseline_controller import BaselineController
from .governor import SafetyGovernor
from .mcp_server import BuildingControlMCPServer
from .structured_logger import StructuredLogger
from .config_manager import ConfigurationManager
from .models import (
    SystemConfig,
    ZoneState,
    ControlDecision,
    SystemHealthState
)


class OrchestrationLoop:
    """
    Main control loop coordinating all system components.
    
    The OrchestrationLoop is the central coordinator that manages the simulation
    lifecycle and orchestrates hourly decision-making cycles. It integrates all
    system components and ensures resilient operation through comprehensive
    error handling and logging.
    
    Architecture:
    - Loads configuration from config.yaml with environment variable overrides
    - Initializes all components with proper dependency injection
    - Manages simulation lifecycle (initialization, execution, shutdown)
    - Executes hourly decision cycles:
      1. Read zone states from DecisionCache
      2. Construct context-rich prompt with building state and energy metrics
      3. Request control decision from LLM via ResilientLLMClient
      4. Validate decision through SafetyGovernor
      5. Write validated decision to DecisionCache
      6. Log all events and metrics
    
    Resilience Features:
    - Automatic fallback to rule-based control on LLM failures
    - Comprehensive exception handling to prevent crashes
    - Health monitoring and status tracking
    - Detailed structured logging for debugging
    
    Attributes:
        config: Complete system configuration
        cache: Thread-safe cache for zone states and decisions
        llm_client: LLM client with retry logic and timeout handling
        baseline: Rule-based controller for fallback scenarios
        governor: Safety validator and fallback coordinator
        mcp_server: MCP server providing tools to LLM (not directly used in orchestration)
        logger: Structured logger for all events
        _decision_cycle_count: Counter for completed decision cycles
        _simulation_start_time: Timestamp when simulation started
        _last_decision_time: Timestamp of last decision cycle (for interval tracking)
    """
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize orchestration loop with configuration loading.
        
        Loads configuration from the specified YAML file, applies environment
        variable overrides, and initializes all system components with proper
        dependency injection.
        
        Args:
            config_path: Path to config.yaml file (default: "config.yaml")
            
        Raises:
            ValueError: If configuration is invalid
            FileNotFoundError: If config file not found and no defaults available
        """
        # Load configuration
        config_manager = ConfigurationManager(config_path)
        self.config = config_manager.load()
        
        # Print configuration for visibility
        print(config_manager.pretty_print(self.config))
        
        # Initialize structured logger
        self.logger = StructuredLogger(
            log_dir=self.config.logging.log_dir,
            log_level=self.config.logging.log_level
        )
        
        self.logger.info(
            component="orchestration_loop",
            event="initialization_start",
            config_path=config_path
        )
        
        # Initialize decision cache (thread-safe storage)
        self.cache = DecisionCache()
        self.logger.info(
            component="orchestration_loop",
            event="component_initialized",
            component_name="DecisionCache"
        )
        
        # Initialize baseline controller for fallback
        self.baseline = BaselineController(self.config.safety)
        self.logger.info(
            component="orchestration_loop",
            event="component_initialized",
            component_name="BaselineController"
        )
        
        # Initialize LLM client with resilience features
        self.llm_client = ResilientLLMClient(
            config=self.config.llm,
            logger=self.logger,
            safety_config=self.config.safety
        )
        self.logger.info(
            component="orchestration_loop",
            event="component_initialized",
            component_name="ResilientLLMClient"
        )
        
        # Initialize safety governor with fallback controller
        self.governor = SafetyGovernor(
            config=self.config.safety,
            baseline_controller=self.baseline,
            logger=self.logger
        )
        self.logger.info(
            component="orchestration_loop",
            event="component_initialized",
            component_name="SafetyGovernor"
        )
        
        # Initialize MCP server (provides tools to LLM, not directly used here)
        self.mcp_server = BuildingControlMCPServer(
            decision_cache=self.cache,
            config=self.config.safety,
            logger=self.logger
        )
        self.logger.info(
            component="orchestration_loop",
            event="component_initialized",
            component_name="BuildingControlMCPServer"
        )
        
        # Initialize state tracking
        self._decision_cycle_count = 0
        self._simulation_start_time: Optional[datetime] = None
        self._last_decision_time: Optional[datetime] = None
        
        # Cumulative energy metrics.  The EnergyPlus runner updates these from
        # real meter readings before each decision cycle.
        self._energy_metrics = {
            "hvac_energy_kwh": 0.0,
            "lighting_energy_kwh": 0.0,
            "total_energy_kwh": 0.0
        }
        
        self.logger.info(
            component="orchestration_loop",
            event="initialization_complete",
            all_components_ready=True
        )
    
    def run_simulation(
        self,
        idf_path: Optional[str] = None,
        epw_path: Optional[str] = None,
        duration_hours: Optional[int] = None
    ) -> None:
        """
        Execute simulation with AI control.
        
        This method would integrate with EnergyPlus via pyenergyplus callbacks.
        For now, it serves as a placeholder demonstrating the intended integration
        pattern and simulation lifecycle management.
        
        NOTE: Full EnergyPlus integration is handled in a separate component
        (EnergyPlusBridge) and will be implemented in a future task.
        
        Args:
            idf_path: Path to IDF building model (overrides config if provided)
            epw_path: Path to EPW weather file (overrides config if provided)
            duration_hours: Simulation duration in hours (for testing/demo)
            
        Raises:
            RuntimeError: If simulation fails to initialize or encounters fatal error
        """
        # Use config paths if not provided as arguments
        idf_path = idf_path or self.config.simulation.idf_path
        epw_path = epw_path or self.config.simulation.epw_path
        
        # Validate paths exist
        if not Path(idf_path).exists():
            raise FileNotFoundError(f"IDF file not found: {idf_path}")
        if not Path(epw_path).exists():
            raise FileNotFoundError(f"EPW file not found: {epw_path}")
        
        # Log simulation start
        self._simulation_start_time = datetime.now()
        self.logger.log_simulation_start(
            idf_path=idf_path,
            epw_path=epw_path,
            decision_interval_hours=self.config.simulation.decision_interval_hours
        )
        
        try:
            # TODO: Integrate with EnergyPlus via pyenergyplus
            # This would involve:
            # 1. Registering EnergyPlus callbacks via EnergyPlusBridge
            # 2. Running EnergyPlus simulation
            # 3. Callbacks write zone states to DecisionCache
            # 4. Orchestration loop reads states and writes decisions
            # 5. Callbacks read decisions and apply to EnergyPlus actuators
            
            # For demonstration, log the intended workflow
            self.logger.info(
                component="orchestration_loop",
                event="simulation_workflow",
                note="EnergyPlus integration will be implemented in EnergyPlusBridge component",
                workflow=[
                    "1. Register EnergyPlus callbacks",
                    "2. Start EnergyPlus simulation",
                    "3. Callbacks write zone states to cache",
                    "4. Orchestration loop executes decision cycles",
                    "5. Callbacks read decisions from cache",
                    "6. Apply decisions to EnergyPlus actuators"
                ]
            )
            
            # Placeholder: In production, EnergyPlus would drive the timing
            # For now, demonstrate a single decision cycle
            if duration_hours:
                self._run_demo_cycles(duration_hours)
            
        except Exception as e:
            self.logger.log_exception(
                component="orchestration_loop",
                exception_type=type(e).__name__,
                message=str(e),
                stack_trace="",  # Would include full traceback in production
                context={"phase": "simulation_execution"}
            )
            raise RuntimeError(f"Simulation failed: {str(e)}") from e
        
        finally:
            # Log simulation end
            self._log_simulation_end()
    
    def execute_decision_cycle(
        self,
        simulation_time: datetime,
        zone_states: Optional[Dict[str, ZoneState]] = None
    ) -> Dict[str, ControlDecision]:
        """
        Execute one hourly decision cycle.
        
        This is the core method that coordinates a single control decision cycle:
        1. Read zone states from cache (or use provided states)
        2. Construct context-rich prompt with building state and energy metrics
        3. Request LLM decision with timeout and retry
        4. Validate decision through Safety Governor
        5. Write validated decision to cache
        6. Log all events and metrics
        
        Process Flow:
        - If zone states are available and LLM is healthy, request AI decision
        - If LLM fails or returns invalid decision, Safety Governor activates fallback
        - All decisions are validated and clamped to safety bounds
        - Comprehensive logging enables debugging and analysis
        
        Args:
            simulation_time: Current simulation timestamp
            zone_states: Current zone states (if None, reads from cache)
            
        Returns:
            Dictionary mapping zone_id to validated ControlDecision objects
            
        Thread Safety:
            Safe to call from orchestration thread. Integrates with thread-safe
            DecisionCache for coordination with EnergyPlus callbacks.
        """
        # Increment cycle counter
        self._decision_cycle_count += 1
        self._last_decision_time = simulation_time
        
        # Read zone states from cache if not provided
        if zone_states is None:
            zone_states = self.cache.read_zone_states()
        
        # Check if we have zone states to work with
        if not zone_states:
            self.logger.warning(
                component="orchestration_loop",
                event="no_zone_states_available",
                simulation_time=simulation_time.isoformat(),
                cycle_count=self._decision_cycle_count
            )
            # Cannot make decisions without zone states
            return {}
        
        # Log decision cycle start with current zone states
        self.logger.log_decision_cycle_start(
            simulation_time=simulation_time,
            zone_states=zone_states
        )
        
        try:
            # Submit the actual request directly.  A short /api/tags probe can
            # time out while a single-GPU Ollama server is busy, even though it
            # can successfully complete the control request.  The request
            # client already has the configured timeout and retry policy.
            llm_response = self.llm_client.request_control_decision(
                zone_states=zone_states,
                energy_metrics=self._energy_metrics,
                simulation_time=simulation_time
            )

            # Log LLM response
            self.logger.log_llm_response(
                success=llm_response.success,
                response_time_ms=llm_response.response_time_ms,
                error_message=llm_response.error_message
            )
            
            # Step 2: Validate through Safety Governor (handles genuine failures)
            validated_decisions = self.governor.validate_and_apply(
                llm_response=llm_response,
                zone_states=zone_states
            )
            
            # Step 4: Write validated decisions to cache for EnergyPlus callbacks
            for decision in validated_decisions.values():
                self.cache.write_decision(decision)
            
            # Step 5: Log energy metrics supplied by the simulation bridge.
            self.logger.log_energy_metrics(
                simulation_time=simulation_time,
                hvac_energy_kwh=self._energy_metrics["hvac_energy_kwh"],
                lighting_energy_kwh=self._energy_metrics["lighting_energy_kwh"],
                total_energy_kwh=self._energy_metrics["total_energy_kwh"]
            )
            
            # Step 6: Log system health state
            self.logger.info(
                component="orchestration_loop",
                event="decision_cycle_complete",
                simulation_time=simulation_time.isoformat(),
                cycle_count=self._decision_cycle_count,
                decisions_count=len(validated_decisions),
                health_state=self.governor.get_health_state().value,
                consecutive_failures=self.governor.get_failure_count()
            )
            
            return validated_decisions
            
        except Exception as e:
            # Log exception and continue with fallback
            self.logger.log_exception(
                component="orchestration_loop",
                exception_type=type(e).__name__,
                message=str(e),
                stack_trace="",  # Would include full traceback in production
                context={
                    "simulation_time": simulation_time.isoformat(),
                    "cycle_count": self._decision_cycle_count
                }
            )
            
            # Use fallback controller for all zones
            fallback_decisions = self.baseline.get_control_decision(
                zone_states=zone_states,
                simulation_time=simulation_time
            )
            
            # Write fallback decisions to cache
            for decision in fallback_decisions.values():
                self.cache.write_decision(decision)
            
            return fallback_decisions
    
    def _create_failure_response(self, error_message: str):
        """
        Create an LLMResponse indicating failure.
        
        Args:
            error_message: Description of failure
            
        Returns:
            LLMResponse with success=False
        """
        from .models import LLMResponse
        return LLMResponse(
            success=False,
            decision=None,
            error_message=error_message,
            response_time_ms=0.0
        )
    
    def update_energy_metrics(self, energy_metrics: Dict[str, float]) -> None:
        """Store cumulative EnergyPlus meter readings for prompts and logging."""
        required_keys = {"hvac_energy_kwh", "lighting_energy_kwh", "total_energy_kwh"}
        missing_keys = required_keys.difference(energy_metrics)
        if missing_keys:
            raise ValueError(f"Missing energy metrics: {sorted(missing_keys)}")

        self._energy_metrics = {
            key: float(energy_metrics[key])
            for key in required_keys
        }
        self.mcp_server.update_energy_metrics(
            hvac_energy_kwh=self._energy_metrics["hvac_energy_kwh"],
            lighting_energy_kwh=self._energy_metrics["lighting_energy_kwh"]
        )
    
    def _run_demo_cycles(self, duration_hours: int) -> None:
        """
        Run demonstration decision cycles without EnergyPlus integration.
        
        This method demonstrates the decision cycle logic by simulating
        zone states and executing control cycles. Used for testing and
        demonstration before full EnergyPlus integration.
        
        Args:
            duration_hours: Number of hourly cycles to simulate
        """
        from .models import ZoneState
        
        self.logger.info(
            component="orchestration_loop",
            event="demo_cycles_start",
            duration_hours=duration_hours
        )
        
        # Simulate zone states and run decision cycles
        base_time = datetime(2024, 7, 25, 9, 0, 0)  # Start at 9 AM
        
        for hour in range(duration_hours):
            simulation_time = base_time.replace(hour=base_time.hour + hour)
            
            # Create mock zone states
            zone_states = {
                "Zone1": ZoneState(
                    zone_id="Zone1",
                    temperature=22.0 + hour * 0.5,  # Gradually warming
                    humidity=0.45,
                    occupancy=5 if 9 <= simulation_time.hour < 17 else 0,
                    pmv=0.1 + hour * 0.05,
                    timestamp=simulation_time
                ),
                "Zone2": ZoneState(
                    zone_id="Zone2",
                    temperature=21.5 + hour * 0.3,
                    humidity=0.50,
                    occupancy=3 if 9 <= simulation_time.hour < 17 else 0,
                    pmv=-0.1 + hour * 0.04,
                    timestamp=simulation_time
                )
            }
            
            # Write zone states to cache (simulating EnergyPlus callbacks)
            for zone_state in zone_states.values():
                self.cache.write_zone_state(zone_state)
            
            # Execute decision cycle
            self.execute_decision_cycle(
                simulation_time=simulation_time,
                zone_states=zone_states
            )
            
            # Small delay between cycles for demonstration
            time.sleep(0.5)
    
    def _log_simulation_end(self) -> None:
        """Log simulation completion statistics."""
        if self._simulation_start_time:
            total_duration = (datetime.now() - self._simulation_start_time).total_seconds()
        else:
            total_duration = 0.0
        
        self.logger.log_simulation_end(
            total_duration_seconds=total_duration,
            decision_cycles_completed=self._decision_cycle_count
        )
    
    def shutdown(self) -> None:
        """
        Gracefully shutdown orchestration loop and close resources.
        
        Ensures all components are properly closed and logs are flushed.
        Should be called when simulation completes or on error.
        """
        self.logger.info(
            component="orchestration_loop",
            event="shutdown_start"
        )
        
        try:
            # Log final statistics
            self._log_simulation_end()
            
            # Close logger (flushes all pending writes)
            self.logger.close()
            
        except Exception as e:
            # Ensure we at least attempt to close logger even if stats fail
            print(f"Error during shutdown: {e}")
            try:
                self.logger.close()
            except:
                pass
    
    def get_system_status(self) -> Dict:
        """
        Get current system status for monitoring and debugging.
        
        Returns:
            Dictionary with current system state including:
            - Health state (HEALTHY, DEGRADED, FALLBACK)
            - Decision cycle count
            - Consecutive LLM failures
            - Energy metrics
            - Last decision time
        """
        return {
            "health_state": self.governor.get_health_state().value,
            "consecutive_failures": self.governor.get_failure_count(),
            "decision_cycles_completed": self._decision_cycle_count,
            "last_decision_time": self._last_decision_time.isoformat() if self._last_decision_time else None,
            "energy_metrics": dict(self._energy_metrics),
            "simulation_running_time_seconds": (
                (datetime.now() - self._simulation_start_time).total_seconds()
                if self._simulation_start_time else 0.0
            )
        }
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures proper shutdown."""
        self.shutdown()
        return False
