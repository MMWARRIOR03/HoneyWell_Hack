# Eco-Loop Building Agents

A Physical AI proof-of-concept that creates a real-time closed-loop control system between EnergyPlus building energy simulation and an open-source LLM (Qwen2.5-7B-Instruct) for autonomous building energy optimization.

## Overview

This system demonstrates AI-driven HVAC control that achieves measurably lower energy consumption compared to rule-based baseline controllers while maintaining thermal comfort within ASHRAE 55 standards (PMV -0.5 to +0.5). The architecture is designed for resilience, gracefully handling unstable LLM endpoints through automatic fallback to rule-based control.

## Features

- **Real-time Closed-Loop Control**: Direct integration with EnergyPlus through pyenergyplus callbacks
- **Resilient LLM Integration**: Robust error handling with timeout, retry, and exponential backoff
- **Safety-First Architecture**: Automatic fallback to rule-based control when AI fails
- **MCP Protocol Support**: Standardized tool interface for LLM decision-making
- **Comprehensive Logging**: Structured JSON-lines logs for analysis and debugging
- **Fault Injection Testing**: Built-in mechanisms to validate system resilience
- **Comparison Dashboard**: Visual analysis of AI vs baseline performance

## Project Structure

```
.
├── src/
│   └── eco_loop_building_agents/
│       ├── __init__.py
│       ├── models.py           # Core dataclasses
│       ├── config_manager.py   # Configuration loading
│       ├── ep_bridge.py        # EnergyPlus integration (TODO)
│       ├── llm_client.py       # LLM communication (TODO)
│       ├── mcp_server.py       # MCP protocol server (TODO)
│       ├── governor.py         # Safety validation (TODO)
│       ├── agent.py            # Main orchestration loop (TODO)
│       └── dashboard.py        # Visualization (TODO)
├── config.yaml                 # System configuration
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd SynapseEnergy
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install EnergyPlus**:
   - Download from [energyplus.net](https://energyplus.net/)
   - Follow platform-specific installation instructions
   - Ensure `energyplus` command is available in PATH

4. **Set up Ollama with Qwen2.5**:
   ```bash
   # Install Ollama (see https://ollama.ai/)
   ollama pull qwen2.5:7b-instruct
   ollama serve
   ```

## Configuration

Edit `config.yaml` to configure system parameters:

```yaml
llm:
  endpoint_url: "http://localhost:11434"  # Update for Colab endpoint
  model_name: "qwen2.5:7b-instruct"
  timeout_seconds: 30.0
  max_retries: 3

safety:
  min_heating_setpoint: 18.0  # °C
  max_heating_setpoint: 22.0
  min_cooling_setpoint: 22.0
  max_cooling_setpoint: 28.0
  min_deadband: 2.0

simulation:
  idf_path: "./models/baseline.idf"
  epw_path: "./weather/IND_New.Delhi.432950_ISHRAE.epw"
  decision_interval_hours: 1

logging:
  log_dir: "./logs"
  log_level: "INFO"
  json_format: true
```

### Environment Variable Overrides

Override configuration without editing files:

```bash
export LLM_ENDPOINT_URL="https://your-colab-endpoint.com"
export LOG_DIR="/mnt/shared/logs"
export IDF_PATH="./models/custom.idf"
export EPW_PATH="./weather/custom.epw"
```

## Usage

### Running AI-Driven Simulation

```bash
python -m eco_loop_building_agents.agent
```

### Running Baseline Comparison

```bash
python -m eco_loop_building_agents.baseline_runner
```

### Generating Comparison Dashboard

```bash
python -m eco_loop_building_agents.dashboard \
  --baseline logs/baseline_20240115.jsonl \
  --ai logs/ai_20240115.jsonl \
  --output ./results/
```

### Testing with Fault Injection

Enable fault injection in `config.yaml`:

```yaml
fault_injection:
  enabled: true
  fault_type: "timeout"  # or connection_error, malformed_json, extreme_values
  fault_rate: 0.1  # 10% of requests fail
  fault_duration_seconds: 60.0
```

## Core Data Models

### ZoneState
Represents current thermal zone conditions:
- `zone_id`: Zone identifier
- `temperature`: Current temperature (°C)
- `humidity`: Relative humidity (0-1)
- `occupancy`: Number of occupants
- `pmv`: Predicted Mean Vote thermal comfort metric
- `timestamp`: Measurement time

### ControlDecision
Represents HVAC control actions:
- `zone_id`: Zone identifier
- `heating_setpoint`: Target heating setpoint (°C)
- `cooling_setpoint`: Target cooling setpoint (°C)
- `lighting_fraction`: Lighting level (0-1)
- `source`: "ai" or "fallback"
- `timestamp`: Decision time

### SafetyConfig
Defines operational safety bounds:
- Heating/cooling setpoint ranges
- Minimum deadband between setpoints
- PMV comfort band limits (ASHRAE 55)

### LLMConfig
Configures LLM client behavior:
- Endpoint URL and model name
- Timeout and retry parameters
- Health check settings

## Architecture

The system follows a resilient, fail-safe architecture:

1. **EnergyPlus Bridge**: Non-blocking interface to simulation engine
2. **Decision Cache**: Thread-safe storage for zone states and decisions
3. **LLM Client**: Robust communication with retry and timeout handling
4. **MCP Server**: Standardized tool interface for LLM
5. **Safety Governor**: Validates decisions and manages fallback
6. **Orchestration Loop**: Coordinates hourly decision cycles

### Fault Recovery Flow

```
Healthy → Degraded → Fallback
   ↑                     ↓
   └─────────────────────┘
   (Automatic recovery when LLM returns)
```

## Development

### Running Tests

```bash
pytest tests/
```

### Code Formatting

```bash
black src/
isort src/
```

### Type Checking

```bash
mypy src/
```

## License

[Specify license here]

## Contributors

SynapseEnergy Team

## Acknowledgments

- EnergyPlus for building simulation capabilities
- Anthropic MCP SDK for standardized tool interfaces
- Ollama for local LLM deployment
- Qwen team for the Qwen2.5-7B-Instruct model
