"""
MCP Server for building control and monitoring tools.

This module implements the Model Context Protocol (MCP) server that provides
standardized tools for LLM agents to query building state and control HVAC
and lighting systems. The server uses Anthropic's mcp Python SDK and integrates
with the DecisionCache for thread-safe reads and writes.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from mcp.server import Server
from mcp.types import Tool, TextContent

from eco_loop_building_agents.decision_cache import DecisionCache
from eco_loop_building_agents.models import SafetyConfig, ControlDecision, ZoneState
from eco_loop_building_agents.structured_logger import StructuredLogger


class BuildingControlMCPServer:
    """
    MCP server providing building control and monitoring tools.
    
    The BuildingControlMCPServer implements the Model Context Protocol to expose
    building state queries and control actions to LLM agents. All tool invocations
    include parameter validation and are logged for debugging and analysis.
    
    Available Tools:
    1. get_zone_state: Query current temperature, humidity, occupancy, and PMV
    2. get_energy_metrics: Query cumulative HVAC and lighting energy consumption
    3. get_grid_carbon_intensity: Query current grid carbon intensity
    4. set_hvac_setpoints: Set heating and cooling setpoints for a zone
    5. set_lighting_level: Set lighting fraction for a zone
    6. get_simulation_logs: Query recent system events and decision history
    
    Key Features:
    - Parameter validation before cache writes to enforce safety bounds
    - Thread-safe integration with DecisionCache
    - Structured logging of all tool invocations
    - Detailed error messages returned to LLM for invalid parameters
    
    Attributes:
        cache: DecisionCache for reading zone states and writing control decisions
        config: SafetyConfig defining operational limits for validation
        logger: StructuredLogger for recording tool invocations and errors
        server: MCP Server instance from Anthropic SDK
        _energy_metrics: Mock storage for energy metrics (to be replaced with EnergyPlus integration)
        _simulation_logs: In-memory buffer for recent log events
    """
    
    def __init__(
        self,
        decision_cache: DecisionCache,
        config: SafetyConfig,
        logger: StructuredLogger
    ):
        """
        Initialize the MCP server with dependencies.
        
        Args:
            decision_cache: DecisionCache for zone states and control decisions
            config: SafetyConfig for parameter validation bounds
            logger: StructuredLogger for recording events
        """
        self.cache = decision_cache
        self.config = config
        self.logger = logger
        self.server = Server("building-control")
        
        # Mock storage for energy metrics (will be replaced with EnergyPlus integration)
        self._energy_metrics: Dict[str, float] = {
            "hvac_energy_kwh": 0.0,
            "lighting_energy_kwh": 0.0,
            "total_energy_kwh": 0.0
        }
        
        # In-memory buffer for recent simulation logs
        self._simulation_logs: List[Dict[str, Any]] = []
        self._max_log_entries = 100
        
        # Register all MCP tool handlers
        self._register_tools()
    
    def _register_tools(self) -> None:
        """
        Register all MCP tool handlers with the server.
        
        Each tool is registered with its handler function, input schema,
        and description for LLM understanding.
        """
        # Tool 1: get_zone_state
        @self.server.call_tool()
        async def get_zone_state(arguments: dict) -> list[TextContent]:
            """
            Query current state of thermal zones.
            
            Parameters:
            - zone_id (optional): Specific zone to query. If omitted, returns all zones.
            
            Returns:
            Temperature, humidity, occupancy, and PMV for specified zones.
            """
            return await self.handle_get_zone_state(arguments)
        
        # Tool 2: get_energy_metrics
        @self.server.call_tool()
        async def get_energy_metrics(arguments: dict) -> list[TextContent]:
            """
            Query cumulative energy consumption metrics.
            
            Parameters: None
            
            Returns:
            HVAC energy (kWh), lighting energy (kWh), and total energy (kWh).
            """
            return await self.handle_get_energy_metrics(arguments)
        
        # Tool 3: get_grid_carbon_intensity
        @self.server.call_tool()
        async def get_grid_carbon_intensity(arguments: dict) -> list[TextContent]:
            """
            Query current grid carbon intensity.
            
            Parameters: None
            
            Returns:
            Carbon intensity in gCO2/kWh based on simulation time.
            """
            return await self.handle_get_grid_carbon_intensity(arguments)
        
        # Tool 4: set_hvac_setpoints
        @self.server.call_tool()
        async def set_hvac_setpoints(arguments: dict) -> list[TextContent]:
            """
            Set heating and cooling setpoints for a thermal zone.
            
            Parameters:
            - zone_id (required): Zone identifier
            - heating_setpoint (required): Target heating setpoint in °C
            - cooling_setpoint (required): Target cooling setpoint in °C
            
            Returns:
            Confirmation of setpoint update or validation error message.
            """
            return await self.handle_set_hvac_setpoints(arguments)
        
        # Tool 5: set_lighting_level
        @self.server.call_tool()
        async def set_lighting_level(arguments: dict) -> list[TextContent]:
            """
            Set lighting level for a thermal zone.
            
            Parameters:
            - zone_id (required): Zone identifier
            - lighting_fraction (required): Lighting level as fraction (0.0-1.0)
            
            Returns:
            Confirmation of lighting update or validation error message.
            """
            return await self.handle_set_lighting_level(arguments)
        
        # Tool 6: get_simulation_logs
        @self.server.call_tool()
        async def get_simulation_logs(arguments: dict) -> list[TextContent]:
            """
            Query recent system events and decision history.
            
            Parameters:
            - lookback_minutes (optional): How many minutes of logs to retrieve (default: 60)
            
            Returns:
            Recent log entries with timestamps and event details.
            """
            return await self.handle_get_simulation_logs(arguments)
    
    async def handle_get_zone_state(self, arguments: dict) -> list[TextContent]:
        """
        Handle get_zone_state tool invocation.
        
        Args:
            arguments: Dict with optional 'zone_id' key
            
        Returns:
            List containing single TextContent with JSON-formatted zone state data
        """
        zone_id = arguments.get("zone_id", None)
        
        # Log tool invocation
        self.logger.info(
            "mcp_server",
            "tool_invocation",
            tool="get_zone_state",
            zone_id=zone_id
        )
        
        try:
            # Read zone states from cache
            zone_states = self.cache.read_zone_states()
            
            if not zone_states:
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "error": "No zone states available yet",
                        "zones": {}
                    })
                )]
            
            # Filter to specific zone if requested
            if zone_id:
                if zone_id not in zone_states:
                    return [TextContent(
                        type="text",
                        text=json.dumps({
                            "error": f"Zone '{zone_id}' not found",
                            "available_zones": list(zone_states.keys())
                        })
                    )]
                zone_states = {zone_id: zone_states[zone_id]}
            
            # Format zone state data for LLM
            result = {"zones": {}}
            for zid, state in zone_states.items():
                result["zones"][zid] = {
                    "temperature": state.temperature,
                    "humidity": state.humidity,
                    "occupancy": state.occupancy,
                    "pmv": state.pmv,
                    "timestamp": state.timestamp.isoformat()
                }
            
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
            
        except Exception as e:
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="get_zone_state",
                error=str(e)
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Failed to retrieve zone state: {str(e)}"})
            )]
    
    async def handle_get_energy_metrics(self, arguments: dict) -> list[TextContent]:
        """
        Handle get_energy_metrics tool invocation.
        
        Args:
            arguments: Dict (no parameters expected)
            
        Returns:
            List containing single TextContent with JSON-formatted energy metrics
        """
        # Log tool invocation
        self.logger.info(
            "mcp_server",
            "tool_invocation",
            tool="get_energy_metrics"
        )
        
        try:
            # Return current energy metrics
            # Note: In production, this would query EnergyPlus meters
            result = {
                "hvac_energy_kwh": self._energy_metrics["hvac_energy_kwh"],
                "lighting_energy_kwh": self._energy_metrics["lighting_energy_kwh"],
                "total_energy_kwh": self._energy_metrics["total_energy_kwh"],
                "timestamp": datetime.now().isoformat()
            }
            
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
            
        except Exception as e:
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="get_energy_metrics",
                error=str(e)
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Failed to retrieve energy metrics: {str(e)}"})
            )]
    
    async def handle_get_grid_carbon_intensity(self, arguments: dict) -> list[TextContent]:
        """
        Handle get_grid_carbon_intensity tool invocation.
        
        Args:
            arguments: Dict (no parameters expected)
            
        Returns:
            List containing single TextContent with JSON-formatted carbon intensity
        """
        # Log tool invocation
        self.logger.info(
            "mcp_server",
            "tool_invocation",
            tool="get_grid_carbon_intensity"
        )
        
        try:
            # Calculate carbon intensity based on time of day
            # Note: This is a simplified model. Production would use real grid data.
            current_hour = datetime.now().hour
            
            # Higher carbon intensity during peak hours (18:00-22:00)
            if 18 <= current_hour < 22:
                carbon_intensity = 650.0  # gCO2/kWh (peak - more coal/gas)
            elif 22 <= current_hour < 24 or 0 <= current_hour < 6:
                carbon_intensity = 550.0  # gCO2/kWh (night - baseload)
            else:
                carbon_intensity = 500.0  # gCO2/kWh (day - more renewables)
            
            result = {
                "carbon_intensity_gco2_per_kwh": carbon_intensity,
                "timestamp": datetime.now().isoformat(),
                "note": "Simplified time-of-day model. Production would use real grid data."
            }
            
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
            
        except Exception as e:
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="get_grid_carbon_intensity",
                error=str(e)
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Failed to retrieve carbon intensity: {str(e)}"})
            )]
    
    async def handle_set_hvac_setpoints(self, arguments: dict) -> list[TextContent]:
        """
        Handle set_hvac_setpoints tool invocation.
        
        Validates parameters against safety bounds before writing to cache.
        
        Args:
            arguments: Dict with 'zone_id', 'heating_setpoint', 'cooling_setpoint'
            
        Returns:
            List containing single TextContent with confirmation or error message
        """
        # Extract and validate required parameters
        zone_id = arguments.get("zone_id")
        heating_setpoint = arguments.get("heating_setpoint")
        cooling_setpoint = arguments.get("cooling_setpoint")
        
        # Log tool invocation
        self.logger.info(
            "mcp_server",
            "tool_invocation",
            tool="set_hvac_setpoints",
            zone_id=zone_id,
            heating_setpoint=heating_setpoint,
            cooling_setpoint=cooling_setpoint
        )
        
        # Validate required parameters are present
        if zone_id is None:
            return [TextContent(
                type="text",
                text=json.dumps({"error": "Missing required parameter: zone_id"})
            )]
        
        if heating_setpoint is None:
            return [TextContent(
                type="text",
                text=json.dumps({"error": "Missing required parameter: heating_setpoint"})
            )]
        
        if cooling_setpoint is None:
            return [TextContent(
                type="text",
                text=json.dumps({"error": "Missing required parameter: cooling_setpoint"})
            )]
        
        try:
            # Convert to float if needed
            heating_setpoint = float(heating_setpoint)
            cooling_setpoint = float(cooling_setpoint)
            
            # Validate setpoints against safety bounds
            is_valid, error_message = self.validate_hvac_setpoints(
                heating_setpoint,
                cooling_setpoint
            )
            
            if not is_valid:
                self.logger.warning(
                    "mcp_server",
                    "validation_failed",
                    tool="set_hvac_setpoints",
                    zone_id=zone_id,
                    error=error_message
                )
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "error": error_message,
                        "safety_bounds": {
                            "min_heating_setpoint": self.config.min_heating_setpoint,
                            "max_heating_setpoint": self.config.max_heating_setpoint,
                            "min_cooling_setpoint": self.config.min_cooling_setpoint,
                            "max_cooling_setpoint": self.config.max_cooling_setpoint,
                            "min_deadband": self.config.min_deadband
                        }
                    })
                )]
            
            # Get current lighting level (preserve existing value if available)
            current_decisions = self.cache.read_all_decisions()
            lighting_fraction = 0.5  # Default
            if zone_id in current_decisions:
                lighting_fraction = current_decisions[zone_id].lighting_fraction
            
            # Create and write control decision to cache
            decision = ControlDecision(
                zone_id=zone_id,
                heating_setpoint=heating_setpoint,
                cooling_setpoint=cooling_setpoint,
                lighting_fraction=lighting_fraction,
                timestamp=datetime.now(),
                source="ai"
            )
            
            self.cache.write_decision(decision)
            
            # Log successful write
            self.logger.info(
                "mcp_server",
                "control_decision_written",
                zone_id=zone_id,
                heating_setpoint=heating_setpoint,
                cooling_setpoint=cooling_setpoint
            )
            
            # Return confirmation
            result = {
                "success": True,
                "zone_id": zone_id,
                "heating_setpoint": heating_setpoint,
                "cooling_setpoint": cooling_setpoint,
                "timestamp": decision.timestamp.isoformat()
            }
            
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
            
        except ValueError as e:
            error_msg = f"Invalid parameter values: {str(e)}"
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="set_hvac_setpoints",
                error=error_msg
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": error_msg})
            )]
        
        except Exception as e:
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="set_hvac_setpoints",
                error=str(e)
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Failed to set HVAC setpoints: {str(e)}"})
            )]
    
    async def handle_set_lighting_level(self, arguments: dict) -> list[TextContent]:
        """
        Handle set_lighting_level tool invocation.
        
        Validates lighting fraction is within 0.0-1.0 range before writing to cache.
        
        Args:
            arguments: Dict with 'zone_id' and 'lighting_fraction'
            
        Returns:
            List containing single TextContent with confirmation or error message
        """
        # Extract required parameters
        zone_id = arguments.get("zone_id")
        lighting_fraction = arguments.get("lighting_fraction")
        
        # Log tool invocation
        self.logger.info(
            "mcp_server",
            "tool_invocation",
            tool="set_lighting_level",
            zone_id=zone_id,
            lighting_fraction=lighting_fraction
        )
        
        # Validate required parameters are present
        if zone_id is None:
            return [TextContent(
                type="text",
                text=json.dumps({"error": "Missing required parameter: zone_id"})
            )]
        
        if lighting_fraction is None:
            return [TextContent(
                type="text",
                text=json.dumps({"error": "Missing required parameter: lighting_fraction"})
            )]
        
        try:
            # Convert to float if needed
            lighting_fraction = float(lighting_fraction)
            
            # Validate lighting fraction is in valid range
            if not 0.0 <= lighting_fraction <= 1.0:
                error_msg = f"Lighting fraction must be between 0.0 and 1.0, got {lighting_fraction}"
                self.logger.warning(
                    "mcp_server",
                    "validation_failed",
                    tool="set_lighting_level",
                    zone_id=zone_id,
                    error=error_msg
                )
                return [TextContent(
                    type="text",
                    text=json.dumps({"error": error_msg})
                )]
            
            # Get current HVAC setpoints (preserve existing values if available)
            current_decisions = self.cache.read_all_decisions()
            if zone_id in current_decisions:
                heating_setpoint = current_decisions[zone_id].heating_setpoint
                cooling_setpoint = current_decisions[zone_id].cooling_setpoint
            else:
                # Use default setpoints if no previous decision exists
                heating_setpoint = 21.0
                cooling_setpoint = 24.0
            
            # Create and write control decision to cache
            decision = ControlDecision(
                zone_id=zone_id,
                heating_setpoint=heating_setpoint,
                cooling_setpoint=cooling_setpoint,
                lighting_fraction=lighting_fraction,
                timestamp=datetime.now(),
                source="ai"
            )
            
            self.cache.write_decision(decision)
            
            # Log successful write
            self.logger.info(
                "mcp_server",
                "control_decision_written",
                zone_id=zone_id,
                lighting_fraction=lighting_fraction
            )
            
            # Return confirmation
            result = {
                "success": True,
                "zone_id": zone_id,
                "lighting_fraction": lighting_fraction,
                "timestamp": decision.timestamp.isoformat()
            }
            
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
            
        except ValueError as e:
            error_msg = f"Invalid parameter values: {str(e)}"
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="set_lighting_level",
                error=error_msg
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": error_msg})
            )]
        
        except Exception as e:
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="set_lighting_level",
                error=str(e)
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Failed to set lighting level: {str(e)}"})
            )]
    
    async def handle_get_simulation_logs(self, arguments: dict) -> list[TextContent]:
        """
        Handle get_simulation_logs tool invocation.
        
        Args:
            arguments: Dict with optional 'lookback_minutes' parameter
            
        Returns:
            List containing single TextContent with recent log entries
        """
        lookback_minutes = arguments.get("lookback_minutes", 60)
        
        # Log tool invocation
        self.logger.info(
            "mcp_server",
            "tool_invocation",
            tool="get_simulation_logs",
            lookback_minutes=lookback_minutes
        )
        
        try:
            # Filter logs to those within lookback window
            cutoff_time = datetime.now()
            # Note: In production, this would parse timestamps from log entries
            # For now, return all buffered logs
            
            result = {
                "lookback_minutes": lookback_minutes,
                "log_entries": self._simulation_logs[-50:],  # Return last 50 entries
                "note": "In-memory buffer. Production would query actual log files."
            }
            
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
            
        except Exception as e:
            self.logger.error(
                "mcp_server",
                "tool_error",
                tool="get_simulation_logs",
                error=str(e)
            )
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Failed to retrieve simulation logs: {str(e)}"})
            )]
    
    def validate_hvac_setpoints(
        self,
        heating: float,
        cooling: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate HVAC setpoints against safety bounds.
        
        Checks:
        1. Heating setpoint is within min/max bounds
        2. Cooling setpoint is within min/max bounds
        3. Cooling setpoint is greater than heating setpoint by at least min_deadband
        
        Args:
            heating: Heating setpoint in °C
            cooling: Cooling setpoint in °C
            
        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if all checks pass, False otherwise
            - error_message: Description of validation failure, or None if valid
        """
        # Check heating setpoint bounds
        if heating < self.config.min_heating_setpoint:
            return False, (
                f"Heating setpoint {heating}°C is below minimum "
                f"{self.config.min_heating_setpoint}°C"
            )
        
        if heating > self.config.max_heating_setpoint:
            return False, (
                f"Heating setpoint {heating}°C exceeds maximum "
                f"{self.config.max_heating_setpoint}°C"
            )
        
        # Check cooling setpoint bounds
        if cooling < self.config.min_cooling_setpoint:
            return False, (
                f"Cooling setpoint {cooling}°C is below minimum "
                f"{self.config.min_cooling_setpoint}°C"
            )
        
        if cooling > self.config.max_cooling_setpoint:
            return False, (
                f"Cooling setpoint {cooling}°C exceeds maximum "
                f"{self.config.max_cooling_setpoint}°C"
            )
        
        # Check deadband requirement
        deadband = cooling - heating
        if deadband < self.config.min_deadband:
            return False, (
                f"Deadband {deadband}°C (cooling - heating) is below minimum "
                f"{self.config.min_deadband}°C. Heating={heating}°C, Cooling={cooling}°C"
            )
        
        # All checks passed
        return True, None
    
    def update_energy_metrics(
        self,
        hvac_energy_kwh: float,
        lighting_energy_kwh: float
    ) -> None:
        """
        Update energy metrics (called by orchestration loop or EnergyPlus bridge).
        
        Args:
            hvac_energy_kwh: Cumulative HVAC energy consumption
            lighting_energy_kwh: Cumulative lighting energy consumption
        """
        self._energy_metrics["hvac_energy_kwh"] = hvac_energy_kwh
        self._energy_metrics["lighting_energy_kwh"] = lighting_energy_kwh
        self._energy_metrics["total_energy_kwh"] = hvac_energy_kwh + lighting_energy_kwh
    
    def add_log_entry(self, log_entry: Dict[str, Any]) -> None:
        """
        Add a log entry to the in-memory buffer.
        
        Args:
            log_entry: Dictionary representing a log event
        """
        self._simulation_logs.append(log_entry)
        
        # Trim buffer if it exceeds maximum size
        if len(self._simulation_logs) > self._max_log_entries:
            self._simulation_logs = self._simulation_logs[-self._max_log_entries:]
    
    def get_server(self) -> Server:
        """
        Get the MCP Server instance for integration with transport layers.
        
        Returns:
            MCP Server instance
        """
        return self.server
