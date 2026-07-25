"""
Unit tests for configuration management.

Tests YAML loading, environment variable overrides, validation,
and configuration formatting.
"""

import pytest
import os
import tempfile
from pathlib import Path
from eco_loop_building_agents.config_manager import ConfigurationManager
from eco_loop_building_agents.models import SystemConfig


class TestConfigurationManager:
    """Tests for ConfigurationManager."""
    
    def test_load_valid_config(self, tmp_path):
        """Test loading valid configuration from YAML file."""
        # Create temporary config file
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
llm:
  endpoint_url: "http://test:11434"
  model_name: "test-model"
  timeout_seconds: 45.0
  max_retries: 5

safety:
  min_heating_setpoint: 19.0
  max_heating_setpoint: 23.0
  min_cooling_setpoint: 23.0
  max_cooling_setpoint: 29.0

simulation:
  idf_path: "./test.idf"
  epw_path: "./test.epw"
  decision_interval_hours: 2

logging:
  log_dir: "./test_logs"
  log_level: "DEBUG"
  json_format: true
""")
        
        # Load configuration
        manager = ConfigurationManager(str(config_file))
        config = manager.load()
        
        # Verify LLM config
        assert config.llm.endpoint_url == "http://test:11434"
        assert config.llm.model_name == "test-model"
        assert config.llm.timeout_seconds == 45.0
        assert config.llm.max_retries == 5
        
        # Verify safety config
        assert config.safety.min_heating_setpoint == 19.0
        assert config.safety.max_heating_setpoint == 23.0
        
        # Verify simulation config
        assert config.simulation.idf_path == "./test.idf"
        assert config.simulation.decision_interval_hours == 2
        
        # Verify logging config
        assert config.logging.log_level == "DEBUG"
    
    def test_load_with_defaults_when_file_missing(self, tmp_path):
        """Test that defaults are used when config file doesn't exist."""
        config_file = tmp_path / "nonexistent.yaml"
        
        manager = ConfigurationManager(str(config_file))
        config = manager.load()
        
        # Should have default values
        assert config.llm.endpoint_url == "http://localhost:11434"
        assert config.llm.model_name == "qwen2.5:7b-instruct"
        assert config.safety.min_heating_setpoint == 18.0
    
    def test_environment_variable_override(self, tmp_path, monkeypatch):
        """Test that environment variables override config file values."""
        # Create config file with default values
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
llm:
  endpoint_url: "http://localhost:11434"

logging:
  log_dir: "./logs"

simulation:
  idf_path: "./models/baseline.idf"
  epw_path: "./weather/default.epw"
""")
        
        # Set environment variables
        monkeypatch.setenv("LLM_ENDPOINT_URL", "http://overridden:11434")
        monkeypatch.setenv("LOG_DIR", "/tmp/override_logs")
        monkeypatch.setenv("IDF_PATH", "./custom.idf")
        monkeypatch.setenv("EPW_PATH", "./custom.epw")
        
        # Load configuration
        manager = ConfigurationManager(str(config_file))
        config = manager.load()
        
        # Verify overrides
        assert config.llm.endpoint_url == "http://overridden:11434"
        assert config.logging.log_dir == "/tmp/override_logs"
        assert config.simulation.idf_path == "./custom.idf"
        assert config.simulation.epw_path == "./custom.epw"
    
    def test_load_with_fault_injection(self, tmp_path):
        """Test loading configuration with fault injection enabled."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
llm:
  endpoint_url: "http://localhost:11434"

safety:
  min_heating_setpoint: 18.0

simulation:
  idf_path: "./test.idf"
  epw_path: "./test.epw"

logging:
  log_dir: "./logs"

fault_injection:
  enabled: true
  fault_type: "connection_error"
  fault_rate: 0.3
  fault_duration_seconds: 90.0
""")
        
        manager = ConfigurationManager(str(config_file))
        config = manager.load()
        
        assert config.fault_injection is not None
        assert config.fault_injection.enabled is True
        assert config.fault_injection.fault_type == "connection_error"
        assert config.fault_injection.fault_rate == 0.3
    
    def test_load_without_fault_injection(self, tmp_path):
        """Test loading configuration without fault injection section."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
llm:
  endpoint_url: "http://localhost:11434"

safety:
  min_heating_setpoint: 18.0

simulation:
  idf_path: "./test.idf"
  epw_path: "./test.epw"

logging:
  log_dir: "./logs"
""")
        
        manager = ConfigurationManager(str(config_file))
        config = manager.load()
        
        # Fault injection should be None when not specified
        assert config.fault_injection is None
    
    def test_pretty_print_format(self, tmp_path):
        """Test pretty printing of configuration."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
llm:
  endpoint_url: "http://localhost:11434"

safety:
  min_heating_setpoint: 18.0
  max_heating_setpoint: 22.0

simulation:
  idf_path: "./test.idf"
  epw_path: "./test.epw"

logging:
  log_dir: "./logs"
  log_level: "INFO"
""")
        
        manager = ConfigurationManager(str(config_file))
        config = manager.load()
        
        # Get pretty printed output
        output = manager.pretty_print(config)
        
        # Verify key sections are present
        assert "[LLM Client]" in output
        assert "[Safety Bounds]" in output
        assert "[Simulation]" in output
        assert "[Logging]" in output
        assert "http://localhost:11434" in output
        assert "18.0°C" in output
    
    def test_validation_with_missing_files(self, tmp_path, capsys):
        """Test validation warns about missing IDF and EPW files."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
llm:
  endpoint_url: "http://localhost:11434"

safety:
  min_heating_setpoint: 18.0

simulation:
  idf_path: "./nonexistent.idf"
  epw_path: "./nonexistent.epw"

logging:
  log_dir: "./logs"
""")
        
        manager = ConfigurationManager(str(config_file))
        
        # Should load successfully but print warnings
        config = manager.load()
        
        # Verify configuration was loaded
        assert config is not None
        
        # Verify warnings were printed
        captured = capsys.readouterr()
        assert "Warning: IDF file not found" in captured.out
        assert "Warning: EPW file not found" in captured.out
    
    def test_partial_config_uses_defaults(self, tmp_path):
        """Test that missing config sections use default values."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
llm:
  endpoint_url: "http://custom:11434"
""")
        
        manager = ConfigurationManager(str(config_file))
        config = manager.load()
        
        # Custom LLM endpoint
        assert config.llm.endpoint_url == "http://custom:11434"
        
        # Default values for other sections
        assert config.llm.timeout_seconds == 30.0
        assert config.safety.min_heating_setpoint == 18.0
        assert config.simulation.decision_interval_hours == 1
        assert config.logging.log_level == "INFO"
