# Eco-Loop Building Agents: System Architecture

## Overview

The Eco-Loop Building Agents system is a Physical AI proof-of-concept that creates a real-time closed-loop control system between EnergyPlus building energy simulation and an open-source LLM (Qwen2.5-7B-Instruct via Ollama) for autonomous building energy optimization.

**Design Philosophy**: This system prioritizes **resilience over performance**. The architecture assumes that the LLM endpoint is unreliable and may fail at any time. Every component implements graceful degradation, and the Safety Governor acts as the central fault-tolerance mechanism ensuring occupant comfort is never compromised regardless of AI system state.

### Key Performance Goals

- **Energy Efficiency**: Demonstrate measurably lower energy consumption compared to rule-based baseline controller
- **Thermal Comfort**: Maintain ASHRAE 55 standards (PMV -0.5 to +0.5) at all times
- **Resilience**: Survive extended simulation runs despite LLM endpoint instability
- **Safety**: Never compromise occupant comfort, even during complete AI system failure

## System Context Diagram

```mermaid
graph TB
    subgraph "External Environment"
        EPW[EPW Weather File]
        IDF[IDF Building Model]
        Colab[Colab-Hosted Ollama Endpoint]
    end
    
    subgraph "Eco-Loop Building Agents System"
        EPBridge[EnergyPlus Bridge]
        Cache[Decision Cache]
        Orch[Orchestration Loop]
        LLM[LLM Client]
        MCP[MCP Server]
        Gov[Safety Governor]
        Fallback[Baseline Controller]
        Log[Structured Logging]
    end
    
    subgraph "Analysis & Visualization"
        Dash[Comparison Dashboard]
        Fault[Fault Injection Harness]
    end
    
    IDF --> EPBridge
    EPW --> EPBridge
    EPBridge <--> Cache
    Cache <--> Orch
    Orch --> LLM
    LLM <--> Colab
    LLM --> MCP
    MCP --> Gov
    Gov --> Fallback
    Gov --> Cache
    Orch --> Log
    Log --> Dash
    Fault -.-> LLM
    Fault -.-> MCP
```

## Architectural Design Decisions

### 1. Non-blocking Decision Cache

**Decision**: EnergyPlus callbacks operate on a separate thread and must never block. The Decision Cache provides thread-safe, non-blocking reads/writes between the simulation engine and the orchestration loop.

**Rationale**: 
- EnergyPlus simulation callbacks run on a tight schedule and blocking them would cause simulation instability
- Using `threading.RLock` for reentrant locking prevents deadlocks
- Non-blocking reads with timeout (default 10ms) ensure callbacks complete quickly
- Stale decisions are retained until new ones arrive (fail-safe behavior)

**Implementation**: The `DecisionCache` class uses polling with 1ms sleep intervals during timeout periods, ensuring minimal overhead while preventing indefinite waits.

### 2. Fail-Safe Defaults

**Decision**: When AI control fails, the system immediately reverts to rule-based control rather than maintaining stale decisions or attempting indefinite retries.

**Rationale**:
- Occupant comfort must never be compromised by AI system failures
- Rule-based control provides predictable, safe behavior
- Automatic fallback enables unattended operation during extended simulations
- Clear health states (HEALTHY → DEGRADED → FALLBACK) make system state observable

**Implementation**: The Safety Governor tracks consecutive failures and activates the Baseline Controller after 3 failures. Recovery is automatic when LLM health checks succeed.

### 3. Separation of Concerns

**Decision**: The LLM client, MCP server, Safety Governor, and EnergyPlus bridge are fully decoupled. Each component can be tested, replaced, or upgraded independently.

**Rationale**:
- Enables component-level testing without full system integration
- Allows swapping LLM endpoints without changing control logic
- Facilitates future enhancements (e.g., different LLM models, alternative safety policies)
- Simplifies debugging through clear component boundaries

**Implementation**: Components communicate through well-defined interfaces (dataclasses for data structures, abstract interfaces for dependencies). The DecisionCache acts as the central data exchange point.

### 4. Observable State

**Decision**: Comprehensive structured logging in JSON-lines format enables post-mortem analysis of multi-day simulation runs without requiring real-time monitoring.

**Rationale**:
- Simulation runs may span days of simulated time (hours of real time)
- Real-time monitoring is not always feasible during hackathon demonstrations
- JSON-lines format enables streaming parsing and analysis
- Structured logs support automated analysis and visualization

**Implementation**: The `StructuredLogger` class writes one JSON object per line with timestamps, component names, event types, and context data. All log files are timestamped for tracking multiple runs.

## Control Flow Architecture

```mermaid
sequenceDiagram
    participant EP as EnergyPlus Engine
    participant EPB as EnergyPlus Bridge
    participant DC as Decision Cache
    participant OL as Orchestration Loop
    participant LLC as LLM Client
    participant MCPS as MCP Server
    participant SG as Safety Governor
    participant BC as Baseline Controller
    
    loop Every Simulation Timestep
        EP->>EPB: Callback: Zone State Update
        EPB->>DC: Write Zone State (non-blocking)
    end
    
    loop Every Hour
        OL->>DC: Read Zone States
        OL->>LLC: Request Control Decision
        alt LLM Available
            LLC->>MCPS: Invoke MCP Tools
            MCPS-->>LLC: Tool Results
            LLC-->>OL: LLM Decision
            OL->>SG: Validate Decision
            SG->>SG: Check Safety Bounds
            SG->>DC: Write Validated Decision
        else LLM Timeout/Failure
            LLC-->>OL: Failure Indicator
            OL->>SG: Notify Failure
            SG->>BC: Activate Fallback
            BC->>DC: Write Rule-Based Decision
        end
    end
    
    loop Every Simulation Timestep
        EPB->>DC: Read Latest Decision (non-blocking)
        EPB->>EP: Apply Setpoints via Actuators
    end
```

## Fault Recovery Architecture

```mermaid
stateDiagram-v2
    [*] --> Healthy: System Start
    Healthy --> Degraded: LLM Timeout/Error
    Healthy --> Healthy: Successful Decision
    Degraded --> Fallback: Health Check Fails
    Fallback --> Degraded: Health Check Succeeds
    Fallback --> Fallback: Continue Rule-Based Control
    Degraded --> Healthy: Successful Decision
    
    note right of Healthy
        AI-Driven Control Active
        LLM provides decisions
        MCP tools enabled
    end note
    
    note right of Degraded
        Attempting AI Control
        Retries with backoff
        Logs warnings
    end note
    
    note right of Fallback
        Rule-Based Control Active
        Baseline Controller engaged
        Periodic health checks
    end note
```

### Health State Transitions

**HEALTHY → DEGRADED**: First LLM failure
- System continues to attempt AI control with retry logic
- Logs warnings but does not activate fallback yet
- Allows for transient network issues

**DEGRADED → FALLBACK**: 3rd consecutive failure
- System switches to rule-based control
- Safety Governor logs fallback activation with trigger reason
- Baseline Controller takes over all control decisions
- Periodic health checks continue in background

**DEGRADED → HEALTHY**: Successful LLM response
- System recovers from degraded state
- Failure counter resets to zero
- Full AI control resumes

**FALLBACK → DEGRADED**: Successful health check after fallback
- System begins recovery path
- Requires one more successful decision to reach HEALTHY
- Gradual recovery prevents oscillation

## System Components

### 1. Decision Cache (`decision_cache.py`)

**Purpose**: Thread-safe, non-blocking storage for zone states and control decisions shared between EnergyPlus callbacks and orchestration loop.

**Key Features**:
- Thread-safe operations using `threading.RLock`
- Non-blocking reads with configurable timeout (default 10ms)
- Separate storage for zone states (read by orchestration) and control decisions (read by EnergyPlus)
- Stale decision retention for fail-safe behavior

**Data Structures**:
```python
@dataclass
class ZoneState:
    zone_id: str
    temperature: float  # °C
    humidity: float  # Relative humidity (0-1)
    occupancy: int  # Number of occupants
    pmv: float  # Predicted Mean Vote
    timestamp: datetime

@dataclass
class ControlDecision:
    zone_id: str
    heating_setpoint: float  # °C
    cooling_setpoint: float  # °C
    lighting_fraction: float  # 0-1
    timestamp: datetime
    source: str  # "ai" or "fallback"
```

**Thread Safety Implementation**:
- `RLock` allows same thread to acquire lock multiple times (reentrant)
- Non-blocking read uses polling with 1ms sleep intervals
- All write operations are atomic with lock acquisition
- Returns shallow copies to prevent external modification

### 2. Resilient LLM Client (`llm_client.py`)

**Purpose**: Robust communication with Colab-hosted Ollama endpoint with comprehensive error handling and retry logic.

**Key Features**:
- Configurable timeout (default 30 seconds)
- Exponential backoff retry (up to 3 attempts)
- Health check before decision requests
- Comprehensive exception handling
- Never blocks indefinitely or crashes on network errors

**Retry Strategy**:
1. **Attempt 1**: Immediate (wait 0 seconds)
2. **Attempt 2**: Wait 2^1 = 2 seconds
3. **Attempt 3**: Wait 2^2 = 4 seconds
4. **If all fail**: Return failure response with error details

**Health Check**:
- Sends minimal prompt to verify endpoint availability
- Short timeout (5 seconds default)
- Called before each control decision request
- Prevents wasting time on known-dead endpoints

**Response Structure**:
```python
@dataclass
class LLMResponse:
    success: bool
    decision: Optional[Dict[str, Any]]
    error_message: Optional[str]
    response_time_ms: float
```

### 3. MCP Server (`mcp_server.py`)

**Purpose**: Implements Model Context Protocol server providing building control and monitoring tools to the LLM using Anthropic's mcp Python SDK.

**Available MCP Tools**:

1. **`get_zone_state`**: Query current state of thermal zones
   - Parameters: `zone_id` (optional, defaults to all zones)
   - Returns: Temperature, humidity, occupancy, PMV for specified zones

2. **`get_energy_metrics`**: Query cumulative energy consumption
   - Returns: HVAC energy (kWh), lighting energy (kWh), total energy (kWh)

3. **`get_grid_carbon_intensity`**: Query current grid carbon intensity
   - Returns: Carbon intensity (gCO2/kWh) based on simulation time

4. **`set_hvac_setpoints`**: Set heating and cooling setpoints for a zone
   - Parameters: `zone_id`, `heating_setpoint`, `cooling_setpoint`
   - Validation: Checks safety bounds before writing to cache

5. **`set_lighting_level`**: Set lighting fraction for a zone
   - Parameters: `zone_id`, `lighting_fraction` (0.0-1.0)
   - Validation: Checks 0-1 range before writing to cache

6. **`get_simulation_logs`**: Query recent system events
   - Parameters: `lookback_minutes` (optional)
   - Returns: Recent log entries with decision history

**Parameter Validation**: All control tools validate parameters against safety bounds before writing to DecisionCache, providing first line of defense before Safety Governor.

### 4. Safety Governor (`governor.py`)

**Purpose**: Validates all control decisions, enforces safety bounds, and manages fallback to rule-based control during AI system failures.

**Key Responsibilities**:
- Validate heating/cooling setpoints against configurable min/max bounds
- Ensure heating setpoint < cooling setpoint (minimum deadband enforcement)
- Clamp invalid values to nearest valid bound
- Activate Baseline Controller when LLM fails (3+ consecutive failures)
- Monitor PMV values and log comfort violations (outside -0.5 to +0.5)
- Track system health state (HEALTHY/DEGRADED/FALLBACK)

**Safety Validation Logic**:

1. **Heating Setpoint Clamping**:
   ```python
   heating = max(min_heating_setpoint, min(heating, max_heating_setpoint))
   ```

2. **Cooling Setpoint Clamping**:
   ```python
   cooling = max(min_cooling_setpoint, min(cooling, max_cooling_setpoint))
   ```

3. **Deadband Enforcement**:
   ```python
   if cooling - heating < min_deadband:
       midpoint = (heating + cooling) / 2
       heating = midpoint - min_deadband / 2
       cooling = midpoint + min_deadband / 2
       # Re-clamp to bounds and adjust if needed
   ```

**Fallback Activation**: After 3 consecutive LLM failures, the Safety Governor transitions to FALLBACK state and uses the Baseline Controller for all control decisions. Recovery is automatic when LLM health checks succeed.

**PMV Monitoring**: Continuously checks all zones for PMV violations outside ASHRAE 55 comfort band (-0.5 to +0.5) and logs warnings for analysis.

### 5. Baseline Controller (`baseline_controller.py`)

**Purpose**: Provides rule-based HVAC control for fallback scenarios and baseline comparison.

**Control Logic**:

**Occupied Hours (9 AM - 5 PM)**:
- Heating Setpoint: 21°C
- Cooling Setpoint: 24°C
- Lighting: 100% (1.0)

**Unoccupied Hours**:
- Heating Setpoint: 18°C (setback)
- Cooling Setpoint: 28°C (setup)
- Lighting: 0% (0.0)

**Design Rationale**:
- Simple time-of-day rules ensure predictable behavior
- No learning or adaptation (pure rule-based)
- Same safety bounds as AI control for fair comparison
- Provides consistent baseline for measuring AI performance

### 6. Orchestration Loop (`orchestration_loop.py`)

**Purpose**: Main control loop that coordinates hourly decision cycles between all system components.

**Key Responsibilities**:
- Load configuration from config.yaml with environment variable overrides
- Initialize all components with proper dependency injection
- Execute decision cycles at hourly intervals
- Construct context-rich prompts for LLM with building state
- Coordinate data flow: read zones → request LLM → validate → write decisions
- Maintain comprehensive structured logging

**Decision Cycle Process**:

1. **Read Zone States**: Retrieve current temperature, humidity, occupancy, PMV from DecisionCache
2. **Construct Prompt**: Build context-rich prompt with:
   - Current zone temperatures and comfort metrics
   - Cumulative energy consumption
   - Time of day and occupancy schedule
   - Grid carbon intensity
   - Recent decision history
3. **Request LLM Decision**: Call LLM client with timeout and retry logic
4. **Validate Decision**: Pass through Safety Governor for bounds checking and fallback
5. **Write Decision**: Store validated decision in DecisionCache for EnergyPlus
6. **Log Events**: Record all metrics, decisions, and health state

**Timing**: Decision cycles execute at hourly intervals during simulation. The orchestration loop runs on a separate thread from EnergyPlus callbacks to prevent blocking.

### 7. EnergyPlus Integration Bridge (`ep_bridge.py`)

**Purpose**: Provides interface between EnergyPlus simulation engine and AI control system through pyenergyplus callbacks.

**Key Responsibilities**:
- Register callback handlers for zone state updates
- Extract zone temperature, humidity, occupancy, PMV from EnergyPlus outputs
- Apply control decisions to HVAC actuators (heating/cooling setpoints) and lighting actuators
- Implement non-blocking reads/writes to DecisionCache
- Wrap all operations in exception handlers to prevent simulation crashes

**Callback Handlers**:

1. **Zone State Update Callback**: Invoked every simulation timestep
   - Reads zone variables from EnergyPlus state object
   - Writes ZoneState to DecisionCache (non-blocking write)
   - Exception-safe (never propagates errors to EnergyPlus)

2. **Actuator Application Callback**: Invoked every simulation timestep
   - Reads ControlDecision from DecisionCache (non-blocking read with timeout)
   - Applies setpoints to EnergyPlus actuators
   - Uses stale decisions if new ones are not available (fail-safe)

**Thread Safety**: All DecisionCache operations use the non-blocking interface to ensure EnergyPlus callbacks complete within required time constraints.

### 8. Structured Logger (`structured_logger.py`)

**Purpose**: Comprehensive, machine-parseable logging for debugging and analysis.

**Log Format**: JSON-lines (one JSON object per line)

**Example Log Entries**:
```json
{"timestamp": "2024-01-15T14:30:00Z", "level": "INFO", "component": "orchestration_loop", "event": "decision_cycle_start", "simulation_time": "2024-07-15T10:00:00"}
{"timestamp": "2024-01-15T14:30:02Z", "level": "INFO", "component": "llm_client", "event": "llm_request", "prompt_length": 1024, "timeout": 30.0}
{"timestamp": "2024-01-15T14:30:05Z", "level": "INFO", "component": "llm_client", "event": "llm_response", "success": true, "response_time_ms": 3245}
{"timestamp": "2024-01-15T14:30:05Z", "level": "INFO", "component": "safety_governor", "event": "decision_validated", "zone": "Zone1", "heating": 20.5, "cooling": 24.0, "modified": false}
```

**Key Event Types**:
- `decision_cycle_start` / `decision_cycle_complete`: Decision cycle boundaries
- `llm_request` / `llm_response`: LLM communication events
- `decision_validated`: Safety Governor validation results
- `pmv_violation`: Thermal comfort violations
- `fallback_activation` / `fallback_deactivation`: Health state transitions
- `exception`: Error events with context

**Log File Management**:
- Separate log file per simulation run with timestamp in filename
- Logs written to configurable directory (default: `./logs`)
- JSON-lines format enables streaming parsing for large files

### 9. Configuration Manager (`config_manager.py`)

**Purpose**: Centralized configuration loading with environment variable overrides and validation.

**Configuration Structure** (`config.yaml`):

```yaml
llm:
  endpoint_url: "http://localhost:11434"
  model_name: "qwen2.5:7b-instruct"
  timeout_seconds: 30.0
  max_retries: 3
  backoff_base: 2.0
  health_check_timeout: 5.0

safety:
  min_heating_setpoint: 18.0  # °C
  max_heating_setpoint: 22.0
  min_cooling_setpoint: 22.0
  max_cooling_setpoint: 28.0
  min_deadband: 2.0
  pmv_min: -0.5
  pmv_max: 0.5

simulation:
  idf_path: "./models/baseline.idf"
  epw_path: "./weather/IND_New.Delhi.432950_ISHRAE.epw"
  decision_interval_hours: 1

logging:
  log_dir: "./logs"
  log_level: "INFO"
  json_format: true

fault_injection:
  enabled: false
  fault_type: "timeout"
  fault_rate: 0.1
  fault_duration_seconds: 60.0
```

**Environment Variable Overrides**:
```bash
LLM_ENDPOINT_URL=https://colab-endpoint.com python agent.py
LOG_DIR=/mnt/shared/logs python agent.py
```

**Validation**: The ConfigurationManager validates all configuration values on load and provides clear error messages for invalid or missing parameters.

### 10. Fault Injection Harness (`fault_injection.py`)

**Purpose**: Deliberately introduce failures to validate system resilience and recovery mechanisms.

**Supported Fault Types**:

1. **LLM Timeout Simulation**: Block LLM requests for configurable duration
2. **Connection Refusal**: Simulate endpoint unavailability
3. **Malformed Response**: Return invalid JSON from LLM mock
4. **Extreme Decisions**: Inject out-of-bounds setpoint values
5. **Intermittent Failures**: Randomly fail requests at configurable rate

**Configuration**:
```yaml
fault_injection:
  enabled: true
  fault_type: "timeout"  # timeout, connection_error, malformed_json, extreme_values
  fault_rate: 0.1  # 10% of requests fail
  fault_duration_seconds: 60.0
```

**Integration**: Fault injector wraps LLM client methods using decorator pattern, allowing faults to be enabled/disabled via configuration without code changes.

### 11. Comparison Dashboard (`dashboard.py`)

**Purpose**: Generate visual comparisons and quantitative metrics for baseline vs AI performance.

**Generated Visualizations**:

1. **Cumulative Energy Consumption Chart**:
   - Line chart comparing baseline vs AI energy use over time
   - Grid carbon intensity overlay as area chart
   - X-axis: Simulation time (days)
   - Y-axis: Energy (kWh)

2. **PMV Comfort Chart**:
   - Scatter plot showing PMV values over time
   - ASHRAE 55 comfort band (-0.5 to +0.5) highlighted
   - Separate series for baseline and AI runs

3. **Summary Statistics Table** (CSV export):
   - Total energy consumption (baseline, AI, % savings)
   - Average PMV (baseline, AI)
   - PMV violations count (baseline, AI)
   - Fallback activation count (AI only)

**Data Flow**: Dashboard reads JSON-lines log files from both baseline and AI simulation runs, parses events, and generates matplotlib visualizations and CSV tables.

## Data Flow

### Write Path (EnergyPlus → AI System)

1. **EnergyPlus Simulation** executes timestep
2. **EnergyPlus Bridge** callback reads zone variables
3. **DecisionCache** stores ZoneState (thread-safe write)
4. **Orchestration Loop** reads zone states (on hourly schedule)
5. **LLM Client** sends zone state context to Ollama endpoint
6. **MCP Server** provides tools for LLM to query additional data

### Read Path (AI System → EnergyPlus)

1. **LLM** returns control decisions via MCP tools
2. **Safety Governor** validates and clamps decisions
3. **DecisionCache** stores ControlDecision (thread-safe write)
4. **EnergyPlus Bridge** callback reads decision (non-blocking)
5. **EnergyPlus Actuators** apply heating/cooling setpoints

### Fallback Path (AI Failure)

1. **LLM Client** detects timeout or error (after retries)
2. **Safety Governor** increments failure counter
3. After **3 consecutive failures**, activate Baseline Controller
4. **Baseline Controller** generates rule-based decisions
5. **DecisionCache** stores fallback decisions (marked with source="fallback")
6. **EnergyPlus** continues operation with rule-based control
7. **Periodic health checks** enable automatic recovery

## Performance Characteristics

### Latency Budget

- **EnergyPlus Timestep**: 15 minutes simulated time (executes in milliseconds)
- **Decision Cache Read Timeout**: 10ms (prevents callback blocking)
- **Decision Cycle Interval**: 1 hour (configurable)
- **LLM Request Timeout**: 30 seconds (configurable)
- **Health Check Timeout**: 5 seconds (configurable)

### Throughput

- **Decision Cycles**: 1 per hour of simulated time (24 cycles per simulated day)
- **EnergyPlus Callbacks**: 4 per simulated hour (every 15 minutes)
- **Log Writes**: Streaming (no buffering for safety)

### Resilience Metrics

- **Maximum Failure Recovery Time**: 3 decision cycles (3 hours simulated time)
- **Fallback Activation Threshold**: 3 consecutive LLM failures
- **Automatic Recovery**: Immediate when health check succeeds
- **Zero Downtime**: System continues operation during all failure modes

## Testing Strategy

### Unit Testing
- Each component tested independently with mocked dependencies
- DecisionCache thread safety tested with concurrent readers/writers
- Safety Governor validation logic tested with boundary cases
- LLM Client retry logic tested with simulated failures

### Integration Testing
- Full system tested with mock EnergyPlus callbacks
- Fault injection used to validate resilience mechanisms
- Baseline vs AI comparison validated with demo simulations

### Demonstration Scenarios
1. **Healthy Operation**: Full AI control with successful LLM responses
2. **Degraded Operation**: Intermittent LLM failures with retry recovery
3. **Fallback Operation**: Complete LLM failure with rule-based control
4. **Recovery**: LLM endpoint recovery with automatic AI control restoration

## Configuration Examples

### Local Development
```yaml
llm:
  endpoint_url: "http://localhost:11434"
  timeout_seconds: 30.0

logging:
  log_dir: "./logs"
  log_level: "DEBUG"
```

### Colab Deployment
```yaml
llm:
  endpoint_url: "https://abc123.ngrok.io"
  timeout_seconds: 45.0
  max_retries: 5

logging:
  log_dir: "/content/drive/MyDrive/synapse_logs"
  log_level: "INFO"

fault_injection:
  enabled: false
```

### Resilience Testing
```yaml
llm:
  endpoint_url: "http://localhost:11434"

fault_injection:
  enabled: true
  fault_type: "timeout"
  fault_rate: 0.2  # 20% failure rate
  fault_duration_seconds: 60.0

logging:
  log_level: "DEBUG"
```

## Deployment Architecture

### Local Development
```
┌─────────────────────┐
│  Developer Machine  │
├─────────────────────┤
│ EnergyPlus          │
│ Eco-Loop Agents     │
│ Ollama (Local)      │
└─────────────────────┘
```

### Hackathon Demo
```
┌──────────────────┐         ┌──────────────────┐
│ Presenter Laptop │◄────────┤ Google Colab     │
├──────────────────┤  HTTPS  ├──────────────────┤
│ EnergyPlus       │         │ Ollama           │
│ Eco-Loop Agents  │         │ Qwen2.5-7B       │
│ Dashboard        │         │ (via ngrok)      │
└──────────────────┘         └──────────────────┘
```

## Key Files and Locations

### Source Code
- `src/eco_loop_building_agents/decision_cache.py` - Thread-safe cache
- `src/eco_loop_building_agents/llm_client.py` - Resilient LLM client
- `src/eco_loop_building_agents/mcp_server.py` - MCP server implementation
- `src/eco_loop_building_agents/governor.py` - Safety validator and fallback
- `src/eco_loop_building_agents/baseline_controller.py` - Rule-based controller
- `src/eco_loop_building_agents/orchestration_loop.py` - Main control loop
- `src/eco_loop_building_agents/ep_bridge.py` - EnergyPlus integration
- `src/eco_loop_building_agents/structured_logger.py` - JSON-lines logging
- `src/eco_loop_building_agents/config_manager.py` - Configuration management
- `src/eco_loop_building_agents/dashboard.py` - Visualization dashboard
- `src/eco_loop_building_agents/fault_injection.py` - Fault injection harness
- `src/eco_loop_building_agents/models.py` - Data structures and types

### Configuration
- `config.yaml` - Main system configuration
- Environment variables for deployment-specific overrides

### Building Models
- `ASHRAE901_OfficeLarge/*.idf` - Building model files
- `IND_New.Delhi.432950_ISHRAE.epw` - Weather data for New Delhi
- `IND_Bangalore.432950_ISHRAE.epw` - Weather data for Bangalore

### Logs and Output
- `logs/*.jsonl` - Structured simulation logs
- `output/` - Dashboard visualizations and statistics

## Security Considerations

### Network Security
- LLM endpoint URL configurable (supports HTTPS)
- No authentication credentials stored in code (use environment variables)
- Timeout mechanisms prevent indefinite hanging on network issues

### Simulation Safety
- All EnergyPlus callbacks wrapped in exception handlers
- Safety Governor enforces hard bounds on all control decisions
- Fallback controller ensures safe operation during AI failures
- PMV monitoring ensures comfort violations are logged

### Data Privacy
- No personally identifiable information collected
- Building state data remains local (only sent to configured LLM endpoint)
- All logs stored locally

## Future Enhancements

### Planned Improvements
1. **Advanced Control Strategies**: Model Predictive Control (MPC) integration
2. **Multi-Zone Coordination**: Optimize HVAC for building-wide efficiency
3. **Learning from History**: Use simulation logs to improve prompts
4. **Carbon Optimization**: Shift loads to periods of low grid carbon intensity
5. **Weather Forecasting**: Incorporate EPW forecast data into control decisions

### Scalability Considerations
- **Multiple Buildings**: Extend MCP tools for multi-building control
- **Distributed LLM**: Support for multiple LLM endpoints with load balancing
- **Real-Time Dashboard**: WebSocket-based live monitoring during simulation
- **Cloud Deployment**: Containerization for scalable cloud deployment

## References

### Technical Standards
- **ASHRAE 55**: Thermal Environmental Conditions for Human Occupancy
- **EnergyPlus Documentation**: https://energyplus.net/documentation
- **Model Context Protocol**: https://modelcontextprotocol.io/

### Dependencies
- **pyenergyplus**: Python API for EnergyPlus simulation
- **Anthropic MCP SDK**: Model Context Protocol implementation
- **Ollama**: Local LLM serving platform
- **Qwen2.5-7B-Instruct**: Open-source instruction-following LLM

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-15  
**Maintained By**: Eco-Loop Building Agents Development Team  
**Related Documents**: `README.md`, `design.md`, `requirements.md`
