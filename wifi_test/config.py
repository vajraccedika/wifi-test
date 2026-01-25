"""Configuration management for wifi-test."""

import os
import subprocess
from pathlib import Path
from typing import Optional

from dotenv import find_dotenv, load_dotenv, set_key


def detect_wifi_interface() -> Optional[str]:
    """Detect active WiFi interface using iw.

    Returns:
        Interface name if found, None otherwise
    """
    try:
        result = subprocess.run(
            ["iw", "dev"], capture_output=True, text=True, check=False, timeout=5
        )

        if result.returncode == 0:
            # Parse output to find first interface
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith("Interface"):
                    interface = line.split()[-1]
                    return interface
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    return None


class Config:
    """Manages configuration for wifi-test application."""

    def __init__(self, env_path: Optional[str] = None):
        """Initialize configuration.

        Args:
            env_path: Path to .env file. If None, searches for .env in parent directories.
        """
        if env_path:
            self.env_path = Path(env_path)
        else:
            # Find .env file or create in current working directory
            env_file = find_dotenv()
            if env_file:
                self.env_path = Path(env_file)
            else:
                self.env_path = Path.cwd() / ".env"

        # Create .env file if it doesn't exist
        if not self.env_path.exists():
            self.env_path.touch()

        # Load environment variables
        load_dotenv(self.env_path)

        # Supported configuration keys (environment variable names)
        self._supported_keys = {
            "DB_PATH",
            "WIFI_INTERFACE",
            "SPEEDTEST_TOOL",
            "IPERF3_BANDWIDTH",
            "IPERF3_SERVER",
            "IPERF3_PORT_RANGE",
            "SCAN_FLUSH",
            "OUTPUT_DIR",
            "PREFIX",
            "GOLDEN_CONFIG_PASSWORD",
            "AUTO_SAVE",
        }

        # Common aliases mapping (uppercased incoming keys -> canonical env key)
        self._aliases = {
            "PATH": "DB_PATH",
            "DBPATH": "DB_PATH",
            "DB": "DB_PATH",
            "INTERFACE": "WIFI_INTERFACE",
            "WIFI_IFACE": "WIFI_INTERFACE",
            "SPEEDTEST": "SPEEDTEST_TOOL",
            "GOLDEN_PASSWORD": "GOLDEN_CONFIG_PASSWORD",
            "GOLDEN_PASS": "GOLDEN_CONFIG_PASSWORD",
        }

        # Allowed values for certain keys
        self._speedtest_allowed = {"speedtest", "iperf3"}
        self._speedtest_aliases = {
            "OOKLA": "speedtest",
            "SPEEDTEST": "speedtest",
            "IPERF": "iperf3",
            "IPERF3": "iperf3",
        }

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get configuration value.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        norm = self._normalize_key(key)
        return os.getenv(norm, default)

    def set(self, key: str, value: str) -> bool:
        """Set configuration value.

        Args:
            key: Configuration key
            value: Configuration value

        Returns:
            True if successful
        """
        norm = self._normalize_key(key)

        if norm not in self._supported_keys:
            raise ValueError(
                f"Unsupported configuration key: '{key}'. Supported keys: {sorted(self._supported_keys)}"
            )

        # Validate specific keys
        if norm == "SPEEDTEST_TOOL":
            if value is None:
                raise ValueError("SPEEDTEST_TOOL requires a value")
            v = str(value).strip()
            # Normalize common aliases for tool name
            v_up = v.upper()
            if v_up in self._speedtest_aliases:
                canonical = self._speedtest_aliases[v_up]
            else:
                canonical = v.lower()

            if canonical not in self._speedtest_allowed:
                raise ValueError(
                    f"Invalid SPEEDTEST_TOOL '{value}'. Allowed: {sorted(self._speedtest_allowed)}"
                )

            # store canonical form
            value = canonical
        # Boolean flags: normalize and validate
        if norm in ("SCAN_FLUSH", "AUTO_SAVE"):
            try:
                b = self._parse_bool(value)
            except ValueError:
                raise ValueError(f"Invalid boolean value for {norm}: '{value}'")
            value = "True" if b else "False"

        # IPERF3 port range validation: expect 'start-end' with valid port numbers
        if norm == "IPERF3_PORT_RANGE":
            v = str(value).strip()
            try:
                parts = v.split("-")
                if len(parts) != 2:
                    raise ValueError()
                start = int(parts[0])
                end = int(parts[1])
                if not (1 <= start <= 65535 and 1 <= end <= 65535 and start <= end):
                    raise ValueError()
                value = f"{start}-{end}"
            except Exception:
                raise ValueError(
                    f"Invalid IPERF3_PORT_RANGE '{value}'. Expected format 'start-end' with ports 1-65535 and start<=end"
                )
        # OUTPUT_DIR: ensure directory exists and is writable
        if norm == "OUTPUT_DIR":
            try:
                p = Path(os.path.expanduser(str(value)))
                if p.exists() and not p.is_dir():
                    raise ValueError(f"OUTPUT_DIR '{p}' exists and is not a directory")
                p.mkdir(parents=True, exist_ok=True)
                if not os.access(str(p), os.W_OK):
                    raise ValueError(f"OUTPUT_DIR '{p}' is not writable")
                value = str(p)
            except Exception as e:
                raise ValueError(f"Invalid OUTPUT_DIR '{value}': {e}")

        # DB_PATH: ensure parent directory exists and is writable; defer creating the DB file until first write
        if norm == "DB_PATH":
            try:
                p = Path(os.path.expanduser(str(value)))
                parent = p.parent if p.parent != Path("") else Path(".")
                parent.mkdir(parents=True, exist_ok=True)
                if not os.access(str(parent), os.W_OK):
                    raise ValueError(
                        f"DB_PATH parent directory '{parent}' is not writable"
                    )
                # Do not create/touch the DB file here; it will be created on first write
                value = str(p)
            except Exception as e:
                raise ValueError(f"Invalid DB_PATH '{value}': {e}")
        set_key(self.env_path, norm, value)
        os.environ[norm] = value
        return True

    def _normalize_key(self, key: str) -> str:
        """Normalize incoming key to canonical env variable name.

        - Uppercases the provided key
        - Applies alias mapping if present
        """
        if not key:
            return key
        k = key.upper()
        return self._aliases.get(k, k)

    def _parse_bool(self, value: str) -> bool:
        """Parse various truthy/falsey inputs into a boolean.

        Accepts: true/false, 1/0, yes/no (case-insensitive).
        Raises ValueError on unrecognized values.
        """
        if isinstance(value, bool):
            return value
        if value is None:
            raise ValueError("None is not a boolean")
        v = str(value).strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
        raise ValueError(f"Unrecognized boolean value: '{value}'")

    def get_all(self) -> dict:
        """Get all configuration values.

        Returns:
            Dictionary of all configuration values
        """
        config = {}
        if self.env_path.exists():
            with open(self.env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        config[key] = value
        return config

    # Configuration getters with defaults
    @property
    def db_path(self) -> str:
        """Get database path."""
        return self.get("DB_PATH", "wifi_data.db")

    @property
    def wifi_interface(self) -> str:
        """Get WiFi interface name.

        Auto-detects interface if not set, or if set to 'default' or 'auto'.
        """
        value = self.get("WIFI_INTERFACE")

        # Auto-detect if not set or set to default/auto
        if value is None or value.lower() in ("default", "auto"):
            detected = detect_wifi_interface()
            if detected:
                # If not set at all, save the detected interface
                if value is None:
                    self.set("WIFI_INTERFACE", detected)
                return detected
            # Fallback if detection fails
            return "wlan0"

        return value

    @property
    def speedtest_tool(self) -> str:
        """Get speedtest tool (speedtest or iperf3)."""
        val = self.get("SPEEDTEST_TOOL", "speedtest")
        if not val:
            return "speedtest"
        v_up = val.upper()
        if v_up in self._speedtest_aliases:
            return self._speedtest_aliases[v_up]
        v = val.lower()
        return v if v in self._speedtest_allowed else "speedtest"

    @property
    def iperf3_server(self) -> Optional[str]:
        """Get iperf3 server address."""
        return self.get("IPERF3_SERVER")

    @property
    def iperf3_port_range(self) -> tuple[int, int]:
        """Get iperf3 server port range as (start, end) tuple.

        Format: "5201-5210" for range, or "5201-5201" for single port.

        Returns:
            Tuple of (start_port, end_port)
        """
        port_range = self.get("IPERF3_PORT_RANGE", "5201-5201")
        try:
            start, end = port_range.split("-")
            return (int(start.strip()), int(end.strip()))
        except (ValueError, AttributeError):
            return (5201, 5201)

    def get_iperf3_ports(self) -> list[int]:
        """Get list of iperf3 ports to try in order.

        Returns:
            List of ports from start to end (inclusive)
        """
        start, end = self.iperf3_port_range
        return list(range(start, end + 1))

    @property
    def iperf3_bandwidth(self) -> str:
        """Get iperf3 bandwidth string used with -b (e.g., '100M')."""
        return self.get("IPERF3_BANDWIDTH", "100M")

    @property
    def scan_flush(self) -> bool:
        """Get whether to flush scan cache before scanning."""
        return self.get("SCAN_FLUSH", "True").lower() == "true"

    @property
    def output_dir(self) -> str:
        """Get output directory for CSV exports."""
        return self.get("OUTPUT_DIR", ".")

    @property
    def prefix(self) -> Optional[str]:
        """Get SSID prefix filter for scanning."""
        return self.get("PREFIX")

    @property
    def golden_config_password(self) -> Optional[str]:
        """Get password for golden config networks."""
        return self.get("GOLDEN_CONFIG_PASSWORD")

    @property
    def auto_save(self) -> bool:
        """Get whether to automatically save scan results to database."""
        return self.get("AUTO_SAVE", "True").lower() == "true"


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get global configuration instance.

    Returns:
        Config instance
    """
    global _config
    if _config is None:
        _config = Config()
    return _config
