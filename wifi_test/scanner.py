"""WiFi scanning functionality."""

import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class WifiScanResult:
    """Represents a single WiFi scan result."""

    bssid: str  # MAC address
    ssid: str
    frequency: float  # MHz
    band: str  # 2.4GHz, 5GHz, 6GHz
    signal: float  # dBm (RSSI)
    channel: Optional[int] = None
    security: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self):
        """Convert to dictionary for database insertion."""
        return asdict(self)


def get_band(freq_mhz: float) -> str:
    """Determine WiFi band from frequency.

    Args:
        freq_mhz: Frequency in MHz

    Returns:
        Band string: 2.4GHz, 5GHz, 6GHz, or Unknown
    """
    if 2400 <= freq_mhz <= 2500:
        return "2.4GHz"
    elif 5000 <= freq_mhz <= 6000:
        return "5GHz"
    elif 6000 <= freq_mhz <= 7125:
        return "6GHz"
    return "Unknown"


def parse_iw_scan_output(output: str) -> List[WifiScanResult]:
    """Parse output from 'iw dev <interface> scan' command.

    Args:
        output: Raw output from iw scan command

    Returns:
        List of WifiScanResult objects
    """
    results = []
    current_bss = None
    current_data = {}

    lines = output.split("\n")

    for line in lines:
        # New BSS entry
        if line.startswith("BSS "):
            # Save previous BSS if it exists
            if (
                current_bss
                and current_data.get("ssid")
                and current_data.get("frequency")
            ):
                result = WifiScanResult(
                    bssid=current_bss,
                    ssid=current_data.get("ssid", ""),
                    frequency=current_data.get("frequency", 0.0),
                    band=get_band(current_data.get("frequency", 0.0)),
                    signal=current_data.get("signal", 0.0),
                    channel=current_data.get("channel"),
                    security=current_data.get("security"),
                    timestamp=datetime.now().isoformat(),
                )
                results.append(result)

            # Start new BSS
            bss_match = re.match(r"BSS ([0-9a-f:]+)", line)
            if bss_match:
                current_bss = bss_match.group(1)
                current_data = {}

        # Parse relevant fields
        elif current_bss:
            line = line.strip()

            # SSID
            if line.startswith("SSID: "):
                ssid = line.replace("SSID: ", "").strip()
                if ssid:  # Ignore empty SSIDs
                    current_data["ssid"] = ssid

            # Frequency
            elif line.startswith("freq: "):
                try:
                    freq = float(line.replace("freq: ", "").strip())
                    current_data["frequency"] = freq
                except ValueError:
                    pass

            # Signal strength (RSSI)
            elif line.startswith("signal: "):
                try:
                    signal_match = re.search(r"(-?\d+\.?\d*)\s*dBm", line)
                    if signal_match:
                        current_data["signal"] = float(signal_match.group(1))
                except ValueError:
                    pass

            # Channel
            elif line.startswith("DS Parameter set: channel "):
                try:
                    channel = int(line.split("channel")[-1].strip())
                    current_data["channel"] = channel
                except ValueError:
                    pass

            # Security (RSN = WPA2/WPA3, WPA = WPA)
            elif line.startswith("RSN:"):
                current_data["security"] = "WPA2/WPA3"
            elif line.startswith("WPA:") and "security" not in current_data:
                current_data["security"] = "WPA"

    # Don't forget the last BSS
    if current_bss and current_data.get("ssid") and current_data.get("frequency"):
        result = WifiScanResult(
            bssid=current_bss,
            ssid=current_data.get("ssid", ""),
            frequency=current_data.get("frequency", 0.0),
            band=get_band(current_data.get("frequency", 0.0)),
            signal=current_data.get("signal", 0.0),
            channel=current_data.get("channel"),
            security=current_data.get("security"),
            timestamp=datetime.now().isoformat(),
        )
        results.append(result)

    return results


def scan_wifi(
    interface: str, flush_cache: bool = True, retries: int = 2
) -> List[WifiScanResult]:
    """Scan for WiFi networks using iw command.

    Args:
        interface: WiFi interface name (e.g., wlan0)
        flush_cache: Whether to flush scan cache before scanning

    Returns:
        List of WifiScanResult objects

    Raises:
        RuntimeError: If scan command fails
    """
    attempt = 0
    last_error = None

    while attempt <= retries:
        try:
            # Optionally flush scan cache for fresh results
            if flush_cache:
                subprocess.run(
                    ["iw", "dev", interface, "scan", "flush"],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )

            # Run the scan
            result = subprocess.run(
                ["iw", "dev", interface, "scan"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            # Parse the output
            scan_results = parse_iw_scan_output(result.stdout)

            return scan_results

        except subprocess.TimeoutExpired:
            last_error = RuntimeError(f"WiFi scan timed out on interface {interface}")
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr or ""
            # Kernel sometimes returns -105 (No buffer space available) when scanning rapidly.
            if "No buffer space available" in err_msg:
                last_error = RuntimeError(
                    "WiFi scan failed: No buffer space available (-105). Retrying..."
                )
            else:
                last_error = RuntimeError(f"WiFi scan failed: {err_msg}")
        except FileNotFoundError:
            raise RuntimeError("iw command not found. Please install iw package.")

        attempt += 1
        time.sleep(1.0)

    # If we exhaust retries, raise the last recorded error
    if last_error:
        raise last_error
    raise RuntimeError("WiFi scan failed after retries")
