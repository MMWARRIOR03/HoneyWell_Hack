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
        self._meter_handles: Dict[str, int] = {}
        self._meter_names = (
            "Electricity:Facility", "NaturalGas:Facility",
            "InteriorLights:Electricity", "ExteriorLights:Electricity",
            "InteriorLights:NaturalGas", "ExteriorLights:NaturalGas",
            "Fans:Electricity", "Pumps:Electricity", "Heating:Electricity",
            "Cooling:Electricity", "HeatRejection:Electricity",
            "Humidifier:Electricity", "HeatRecovery:Electricity",
            "Fans:NaturalGas", "Pumps:NaturalGas", "Heating:NaturalGas",
            "Cooling:NaturalGas", "HeatRejection:NaturalGas",
            "Humidifier:NaturalGas", "HeatRecovery:NaturalGas",
        )
        self._energy_metrics: Dict[str, float] = {
            "hvac_energy_kwh": 0.0,
            "lighting_energy_kwh": 0.0,
            "total_energy_kwh": 0.0,
        }
        self._initialized = False
        self._api = None  # Store API reference for v26.1 compatibility
        self._api_ready_wait_logged = False
    
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
            api.runtime.callback_end_zone_timestep_after_zone_reporting(
                state,
                self._callback_energy_timestep
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
                # This callback is also invoked during sizing.  At that point
                # EnergyPlus has not necessarily registered all data-exchange
                # points yet, so every handle lookup can legitimately return
                # -1.  Do not treat that transient state as initialization.
                if not self._api.exchange.api_data_fully_ready(state):
                    if not self._api_ready_wait_logged:
                        self.logger.info(
                            "ep_bridge",
                            "waiting_for_api_data",
                            note="Deferring handle lookup until EnergyPlus data exchange is ready"
                        )
                        self._api_ready_wait_logged = True
                    return
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
            state: Energy

Plus state handle (integer in v26.1+)
            
        Raises:
            Exception: If critical handles cannot be obtained (logged and re-raised)
        """
        try:
            # Initialization may be retried after a transient failure; clear
            # any partial data before rebuilding the handle maps.
            self._zone_ids.clear()
            self._actuator_handles.clear()
            self._output_variable_handles.clear()
            self._meter_handles.clear()

            # For EnergyPlus v26.1+, state is just an integer handle
            # We need to use the API's data_transfer methods to get zone information
            # Instead of accessing state.dataGlobal directly
            
            # Use API exchange to get available actuators and output variables
            # This is a workaround - we'll discover zones from the building model
            # For now, hardcode common zone names from ASHRAE 901 model
            
            # Zone names from ASHRAE 901 Large Office baseline model (New Delhi)
            # Extracted from models/baseline.idf - must match exact capitalization
            default_zones = [
                "Basement",
                "Core_bottom", "Core_mid", "Core_top",
                "Perimeter_bot_ZN_1", "Perimeter_bot_ZN_2", "Perimeter_bot_ZN_3", "Perimeter_bot_ZN_4",
                "Perimeter_mid_ZN_1", "Perimeter_mid_ZN_2", "Perimeter_mid_ZN_3", "Perimeter_mid_ZN_4",
                "Perimeter_top_ZN_1", "Perimeter_top_ZN_2", "Perimeter_top_ZN_3", "Perimeter_top_ZN_4",
                "GroundFloor_Plenum", "MidFloor_Plenum", "TopFloor_Plenum",
                "DataCenter_bot_ZN_6", "DataCenter_mid_ZN_6", "DataCenter_top_ZN_6", "DataCenter_basement_ZN_6"
            ]
            
            self.logger.info(
                "ep_bridge",
                "initializing_handles",
                note="Using default zone list for EnergyPlus v26.1+",
                num_zones=len(default_zones)
            )

            # Meter values exposed by the EnergyPlus API are the energy for
            # the current timestep in joules, not cumulative readings.  Keep
            # handles here and accumulate them in the end-of-timestep callback.
            # Missing optional end-use meters are tolerated; facility meters
            # remain the authoritative total-energy measurement.
            for meter_name in self._meter_names:
                handle = self._api.exchange.get_meter_handle(state, meter_name)
                if handle != -1:
                    self._meter_handles[meter_name] = handle

            self.logger.info(
                "ep_bridge",
                "meter_handles_initialized",
                meter_count=len(self._meter_handles),
                meters=sorted(self._meter_handles),
            )
            
            # For each zone, try to get handles
            for zone_name in default_zones:
                # Try to get actuator handles
                try:
                    # Get heating setpoint actuator
                    heating_handle = self._api.exchange.get_actuator_handle(
                        state,
                        "Zone Temperature Control",
                        "Heating Setpoint",
                        zone_name
                    )
                    
                    # Get cooling setpoint actuator
                    cooling_handle = self._api.exchange.get_actuator_handle(
                        state,
                        "Zone Temperature Control",
                        "Cooling Setpoint",
                        zone_name
                    )
                    
                    # Only add zone if we successfully got handles
                    if heating_handle != -1 and cooling_handle != -1:
                        self._zone_ids.append(zone_name)
                        
                        # Initialize handle dictionaries for this zone
                        self._actuator_handles[zone_name] = {
                            "heating_setpoint": heating_handle,
                            "cooling_setpoint": cooling_handle
                        }
                        self._output_variable_handles[zone_name] = {}
                        
                        # Get output variable handles
                        temp_handle = self._api.exchange.get_variable_handle(
                            state,
                            "Zone Mean Air Temperature",
                            zone_name
                        )
                        humidity_handle = self._api.exchange.get_variable_handle(
                            state,
                            "Zone Air Relative Humidity",
                            zone_name
                        )
                        pmv_handle = self._api.exchange.get_variable_handle(
                            state,
                            "Zone Thermal Comfort Fanger Model PMV",
                            zone_name
                        )
                        occupancy_handle = self._api.exchange.get_variable_handle(
                            state,
                            "Zone People Occupant Count",
                            zone_name
                        )
                        
                        if temp_handle != -1:
                            self._output_variable_handles[zone_name]["temperature"] = temp_handle
                        if humidity_handle != -1:
                            self._output_variable_handles[zone_name]["humidity"] = humidity_handle
                        if pmv_handle != -1:
                            self._output_variable_handles[zone_name]["pmv"] = pmv_handle
                        if occupancy_handle != -1:
                            self._output_variable_handles[zone_name]["occupancy"] = occupancy_handle
                        
                        self.logger.info(
                            "ep_bridge",
                            "zone_handles_obtained",
                            zone_name=zone_name,
                            has_heating=heating_handle != -1,
                            has_cooling=cooling_handle != -1,
                            has_temp=temp_handle != -1,
                            has_humidity=humidity_handle != -1,
                            has_pmv=pmv_handle != -1,
                            has_occupancy=occupancy_handle != -1
                        )
                except Exception as e:
                    # Zone might not exist in this model, skip it
                    continue
            
            # Mark as initialized
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

    def _callback_energy_timestep(self, state: Any) -> None:
        """Accumulate EnergyPlus meter values after each reported zone timestep."""
        try:
            if not self._initialized or self._api.exchange.warmup_flag(state):
                return

            # Some facility-level meters are registered only after EnergyPlus
            # leaves sizing and enters the run period.  Retry unresolved
            # handles here instead of permanently treating them as absent.
            for meter_name in self._meter_names:
                if meter_name not in self._meter_handles:
                    handle = self._api.exchange.get_meter_handle(state, meter_name)
                    if handle != -1:
                        self._meter_handles[meter_name] = handle
                        self.logger.info(
                            "ep_bridge",
                            "meter_handle_obtained_late",
                            meter=meter_name,
                        )

            joules_per_kwh = 3_600_000.0

            def meter_kwh(*names: str) -> float:
                return sum(
                    self._api.exchange.get_meter_value(state, self._meter_handles[name])
                    / joules_per_kwh
                    for name in names
                    if name in self._meter_handles
                )

            # Facility meters avoid double counting all end uses.  HVAC and
            # lighting are separately reported end-use subsets for comparison.
            facility_kwh = meter_kwh("Electricity:Facility", "NaturalGas:Facility")
            lighting_kwh = meter_kwh(
                "InteriorLights:Electricity", "ExteriorLights:Electricity",
                "InteriorLights:NaturalGas", "ExteriorLights:NaturalGas",
            )
            hvac_kwh = meter_kwh(
                "Fans:Electricity", "Pumps:Electricity", "Heating:Electricity",
                "Cooling:Electricity", "HeatRejection:Electricity",
                "Humidifier:Electricity", "HeatRecovery:Electricity",
                "Fans:NaturalGas", "Pumps:NaturalGas", "Heating:NaturalGas",
                "Cooling:NaturalGas", "HeatRejection:NaturalGas",
                "Humidifier:NaturalGas", "HeatRecovery:NaturalGas",
            )

            self._energy_metrics["total_energy_kwh"] += facility_kwh
            self._energy_metrics["lighting_energy_kwh"] += lighting_kwh
            self._energy_metrics["hvac_energy_kwh"] += hvac_kwh
        except Exception as e:
            self.logger.log_exception(
                "ep_bridge",
                type(e).__name__,
                str(e),
                traceback.format_exc(),
                {"context": "callback_energy_timestep"},
            )

    def get_energy_metrics(self) -> Dict[str, float]:
        """Return cumulative EnergyPlus-derived energy values in kWh."""
        return dict(self._energy_metrics)
    
    def _extract_and_cache_zone_states(self, state: Any) -> None:
        """
        Extract zone state from EnergyPlus and write to DecisionCache.
        
        Reads temperature, humidity, occupancy, and PMV from EnergyPlus output
        variables and creates ZoneState objects that are written to the cache
        for consumption by the orchestration loop.
        
        Args:
            state: EnergyPlus state handle (integer in v26.1+)
            
        Exception Safety:
            All exceptions caught and logged without propagation
        """
        try:
            for zone_id in self._zone_ids:
                # Read output variables using API exchange methods
                temperature = self._api.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id].get("temperature", -1)
                ) if "temperature" in self._output_variable_handles[zone_id] else 22.0
                
                humidity_percent = self._api.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id].get("humidity", -1)
                ) if "humidity" in self._output_variable_handles[zone_id] else 50.0
                # Convert from percentage to fraction
                humidity = humidity_percent / 100.0
                
                occupancy = int(round(self._api.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id]["occupancy"]
                ))) if "occupancy" in self._output_variable_handles[zone_id] else 0
                
                pmv = self._api.exchange.get_variable_value(
                    state,
                    self._output_variable_handles[zone_id].get("pmv", -1)
                ) if "pmv" in self._output_variable_handles[zone_id] else 0.0
                
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
                heating_handle = self._actuator_handles[zone_id].get("heating_setpoint", -1)
                if heating_handle != -1:  # Valid handle
                    self._api.exchange.set_actuator_value(
                        state,
                        heating_handle,
                        decision.heating_setpoint
                    )
                
                # Apply cooling setpoint
                cooling_handle = self._actuator_handles[zone_id].get("cooling_setpoint", -1)
                if cooling_handle != -1:  # Valid handle
                    self._api.exchange.set_actuator_value(
                        state,
                        cooling_handle,
                        decision.cooling_setpoint
                    )
                
                # Apply lighting level (if handle exists)
                lighting_handle = self._actuator_handles[zone_id].get("lighting", -1)
                if lighting_handle != -1:  # Valid handle
                    self._api.exchange.set_actuator_value(
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
