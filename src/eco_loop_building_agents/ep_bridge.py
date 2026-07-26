"""
EnergyPlus Integration Bridge for non-blocking communication between
EnergyPlus simulation engine and AI control system.

This module provides the EnergyPlusBridge class that operates on the EnergyPlus
callback thread and must never block the simulation. It reads zone states from
EnergyPlus, writes them to DecisionCache, reads control decisions from DecisionCache,
and applies them to EnergyPlus actuators.
"""

import traceback
from typing import Any, Dict, Optional
from datetime import datetime

from eco_loop_building_agents.decision_cache import DecisionCache
from eco_loop_building_agents.structured_logger import StructuredLogger
from eco_loop_building_agents.models import ZoneState, ControlDecision


class EnergyPlusBridge:
    """
    Thread-safe bridge between EnergyPlus and AI control system.
    
    The EnergyPlusBridge operates on the EnergyPlus callback thread and must be
    completely non-blocking and exception-safe to prevent simulation crashes.
    
    Key Responsibilities:
    - Register callback handlers for zone state updates using pyenergyplus API
    - Extract zone temperature, humidity, occupancy, and PMV from EnergyPlus
    - Write zone states to DecisionCache (non-blocking)
    - Read control decisions from DecisionCache (non-blocking with timeout)
    - Apply heating/cooling setpoints and lighting levels to EnergyPlus actuators
    - Wrap all operations in exception handlers
    
    Design Principles:
    - Never block: All DecisionCache operations use non-blocking reads with timeout
    - Exception safety: All callbacks wrapped in try-except to prevent crashes
    - Fail-safe: Stale decisions are maintained if new ones are unavailable
    - Observable: All exceptions and key events logged for debugging
    
    Attributes:
        cache: Thread-safe cache for zone states and control decisions
        logger: Structured logger for callback events
        _zone_ids: List of zone identifiers in the simulation
        _actuator_handles: Dictionary storing actuator handles for each zone
        _output_variable_handles: Dictionary storing output variable handles
        _initialized: Flag indicating if handles have been initialized
    """
    
    def __init__(self, decision_cache: DecisionCache, logger: StructuredLogger):
        """
        Initialize bridge with shared decision cache and logger.
        
        Args:
            decision_cache: Thread-safe cache for zone states and decisions
            logger: Structured logger for callback events
        """
        self.cache = decision_cache
        self.logger = logger
        self._zone_ids: list[str] = []
        self._actuator_handles: Dict[str, Dict[str, int]] = {}
        self._output_variable_handles: Dict[str, Dict[str, int]] = {}
        self._initialized = False
        self._api = None  # Store API reference for v26.1 compatibility
    
    def register_callbacks(self, api: Any, state: Any) -> None:
        """
        Register EnergyPlus callback handlers.
        
        This method registers the zone timestep callback that will be invoked
        by EnergyPlus on every timestep after heat balance initialization.
        
        For EnergyPlus v26.1+, callbacks are registered through the API object
        with the state handle passed as a parameter.
        
        Args:
            api: EnergyPlus API object from pyenergyplus.api.EnergyPlusAPI
            state: EnergyPlus state handle (integer in v26.1+)
            
        Exception Safety:
            All exceptions caught and logged without propagation to EnergyPlus
        """
        try:
            # Store API reference for later use
            self._api = api
            
            # Register the zone timestep callback using v26.1+ API
            api.runtime.callback_begin_zone_timestep_after_init_heat_balance(
                state,
                self._callback_zone_timestep
            )
            
            self.logger.info(
                "ep_bridge",
                "callbacks_registered",
                callback_type="begin_zone_timestep_after_init_heat_balance",
                api_version="v26.1+"
            )
            
        except Exception as e:
            self.logger.log_exception(
                "ep_bridge",
                type(e).__name__,
                str(e),
                traceback.format_exc(),
                {"context": "register_callbacks"}
            )
    
    def _callback_zone_timestep(self, state: Any) -> None:
        """
        Callback invoked by EnergyPlus on every zone timestep.
        
        This method is called by EnergyPlus and must be non-blocking and exception-safe.
        It performs two operations:
        1. Read zone states from EnergyPlus and write to DecisionCache
        2. Read control decisions from DecisionCache and apply to actuators
        
        Args:
            state: EnergyPlus state object from pyenergyplus
            
        Exception Safety:
            All exceptions caught and logged. No exceptions propagate to EnergyPlus.
        """
        try:
            # Initialize handles on first callback
            if not self._initialized:
                self._initialize_handles(state)
            
            # Extract zone states and write to cache
            self._extract_and_cache_zone_states(state)
            
            # Read control decisions and apply to actuators
            self._apply_control_decisions(state)
            
        except Exception as e:
            # Critical: Log exception but do NOT propagate to EnergyPlus
            self.logger.log_exception(
                "ep_bridge",
                type(e).__name__,
                str(e),
                traceback.format_exc(),
                {"context": "callback_zone_timestep"}
            )
    
    def _initialize_handles(self, state: Any) -> None:
        """
        Initialize actuator and output variable handles on first callback.
        
        This method discovers all thermal zones in the simulation and obtains
        handles for:
        - Output variables: Zone Mean Air Temperature, Zone Air Relative Humidity,
                           Zone People Occupant Count, Zone Thermal Comfort Fanger Model PMV
        - Actuators: Zone Temperature Control heating/cooling setpoints,
                    Zone Lights Electric Input Power Level
        
        Args:
            state: EnergyPlus state object from pyenergyplus
            
        Raises:
            Exception: If critical handles cannot be obtained (logged and re-raised)
        """
        try:
            # Get number of zones
            num_zones = state.dataGlobal.NumOfZones
            
            self.logger.info(
                "ep_bridge",
                "initializing_handles",
                num_zones=num_zones
            )
            
            # For each zone, get handles
            for zone_idx in range(1, num_zones + 1):
                # Get zone name
                zone_name = state.dataHeatBal.Zone(zone_idx).Name
                self._zone_ids.append(zone_name)
                
                # Initialize handle dictionaries for this zone
                self._actuator_handles[zone_name] = {}
                self._output_variable_handles[zone_name] = {}
                
                # Get output variable handles
                temp_handle = state.exchange.get_variable_handle(
                    state,
                    "Zone Mean Air Temperature",
                    zone_name
                )
                self._output_variable_handles[zone_name]["temperature"] = temp_handle
                
                humidity_handle = state.exchange.get_variable_handle(
                    state,
                    "Zone Air Relative Humidity",
                    zone_name
                )
                self._output_variable_handles[zone_name]["humidity"] = humidity_handle
                
                occupancy_handle = state.exchange.get_variable_handle(
                    state,
                    "Zone People Occupant Count",
                    zone_name
                )
                self._output_variable_handles[zone_name]["occupancy"] = occupancy_handle
                
                pmv_handle = state.exchange.get_variable_handle(
                    state,
                    "Zone Thermal Comfort Fanger Model PMV",
                    zone_name
                )
                self._output_variable_handles[zone_name]["pmv"] = pmv_handle
                
                # Get actuator handles for heating setpoint
                heating_handle = state.exchange.get_actuator_handle(
                    state,
                    "Zone Temperature Control",
                    "Heating Setpoint",
                    zone_name
                )
                self._actuator_handles[zone_name]["heating_setpoint"] = heating_handle
                
                # Get actuator handles for cooling setpoint
                cooling_handle = state.exchange.get_actuator_handle(
                    state,
                    "Zone Temperature Control",
                    "Cooling Setpoint",
                    zone_name
                )
                self._actuator_handles[zone_name]["cooling_setpoint"] = cooling_handle
                
                # Get actuator handle for lighting
                lighting_handle = state.exchange.get_actuator_handle(
                    state,
                    "Lights",
                    "Electric Power Level",
                    zone_name + " Lights"  # Typical naming convention
                )
                self._actuator_handles[zone_name]["lighting"] = lighting_handle
                
                self.logger.debug(
                    "ep_bridge",
                    "zone_handles_initialized",
                    zone=zone_name,
                    zone_index=zone_idx
                )
            
            self._initialized = True
            
            self.logger.info(
                "ep_bridge",
                "handles_initialized",
                zone_count=len(self._zone_ids),
                zones=self._zone_ids
            )
            
        except Exception as e:
            self.logger.log_exception(
                "ep_bridge",
                type(e).__name__,
                str(e),
                traceback.format_exc(),
                {"context": "initialize_handles"}
            )
            # Re-raise as initialization failure is critical
            raise
    
    def _extract_and_cache_zone_states(self, state: Any) -> None:
        """
        Extract zone state from EnergyPlus and write to DecisionCache.
        
        Reads temperature, humidity, occupancy, and PMV from EnergyPlus output
        variables and creates ZoneState objects that are written to the cache
        for consumption by the orchestration loop.
        
        Args:
            state: EnergyPlus state object from pyenergyplus
            
        Exception Safety:
            All exceptions caught and logged without propagation
        """
        try:
            for zone_id in self._zone_ids:
                # Read output variables
                temperature = state.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id]["temperature"]
                )
                
                humidity_percent = state.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id]["humidity"]
                )
                # Convert from percentage to fraction
                humidity = humidity_percent / 100.0
                
                occupancy_float = state.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id]["occupancy"]
                )
                occupancy = int(occupancy_float)
                
                pmv = state.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id]["pmv"]
                )
                
                # Create ZoneState object
                zone_state = ZoneState(
                    zone_id=zone_id,
                    temperature=temperature,
                    humidity=humidity,
                    occupancy=occupancy,
                    pmv=pmv,
                    timestamp=datetime.now()
                )
                
                # Write to cache (non-blocking)
                self.cache.write_zone_state(zone_state)
                
                self.logger.debug(
                    "ep_bridge",
                    "zone_state_cached",
                    zone=zone_id,
                    temperature=temperature,
                    humidity=humidity,
                    occupancy=occupancy,
                    pmv=pmv
                )
                
        except Exception as e:
            self.logger.log_exception(
                "ep_bridge",
                type(e).__name__,
                str(e),
                traceback.format_exc(),
                {"context": "extract_and_cache_zone_states"}
            )
    
    def _apply_control_decisions(self, state: Any) -> None:
        """
        Read control decisions from DecisionCache and apply to actuators.
        
        Performs non-blocking reads with timeout from the DecisionCache to obtain
        control decisions. If decisions are available, applies them to EnergyPlus
        actuators for heating setpoints, cooling setpoints, and lighting levels.
        
        If no decision is available within timeout (10ms), maintains previous
        actuator values (fail-safe behavior).
        
        Args:
            state: EnergyPlus state object from pyenergyplus
            
        Exception Safety:
            All exceptions caught and logged without propagation
        """
        try:
            for zone_id in self._zone_ids:
                # Non-blocking read with 10ms timeout
                decision = self.cache.read_decision(zone_id, timeout_ms=10)
                
                if decision is None:
                    # No decision available - maintain previous values
                    self.logger.debug(
                        "ep_bridge",
                        "no_decision_available",
                        zone=zone_id,
                        behavior="maintaining_previous_values"
                    )
                    continue
                
                # Apply heating setpoint
                heating_handle = self._actuator_handles[zone_id]["heating_setpoint"]
                if heating_handle != -1:  # Valid handle
                    state.exchange.set_actuator_value(
                        state,
                        heating_handle,
                        decision.heating_setpoint
                    )
                
                # Apply cooling setpoint
                cooling_handle = self._actuator_handles[zone_id]["cooling_setpoint"]
                if cooling_handle != -1:  # Valid handle
                    state.exchange.set_actuator_value(
                        state,
                        cooling_handle,
                        decision.cooling_setpoint
                    )
                
                # Apply lighting level
                lighting_handle = self._actuator_handles[zone_id]["lighting"]
                if lighting_handle != -1:  # Valid handle
                    state.exchange.set_actuator_value(
                        state,
                        lighting_handle,
                        decision.lighting_fraction
                    )
                
                self.logger.debug(
                    "ep_bridge",
                    "control_applied",
                    zone=zone_id,
                    heating_setpoint=decision.heating_setpoint,
                    cooling_setpoint=decision.cooling_setpoint,
                    lighting_fraction=decision.lighting_fraction,
                    decision_source=decision.source,
                    decision_timestamp=decision.timestamp.isoformat()
                )
                
        except Exception as e:
            self.logger.log_exception(
                "ep_bridge",
                type(e).__name__,
                str(e),
                traceback.format_exc(),
                {"context": "apply_control_decisions"}
            )
    
    def get_zone_ids(self) -> list[str]:
        """
        Get list of zone identifiers discovered during initialization.
        
        Returns:
            List of zone IDs. Empty list if not yet initialized.
        """
        return self._zone_ids.copy()
    
    def is_initialized(self) -> bool:
        """
        Check if bridge has been initialized with EnergyPlus handles.
        
        Returns:
            True if handles have been obtained, False otherwise
        """
        return self._initialized
