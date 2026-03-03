"""Speedtest functionality for wifi-test."""

import json
import logging
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Optional

from .utils import run_cmd


def _bits_to_mbps(bits_per_second: float | int | None) -> float:
    """Convert bits per second to megabits per second."""
    return round((bits_per_second or 0) / 1_000_000, 2)


def _get_nested(data: dict, *keys, default=None):
    """Safely get nested dictionary value.

    Args:
        data: Source dictionary
        *keys: Sequence of keys to traverse
        default: Value to return if path doesn't exist

    Returns:
        Value at nested path, or default if not found
    """
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return default
        if data is None:
            return default
    return data


@dataclass
class SpeedtestResult:
    """Represents a single speedtest result."""

    tool: str  # 'ookla' or 'iperf3'
    ssid: Optional[str] = None  # WiFi network SSID
    bssid: Optional[str] = None  # Access point BSSID (MAC)
    download_mbps: float = 0.0  # Download speed in Mbps
    upload_mbps: float = 0.0  # Upload speed in Mbps
    ping_ms: float = 0.0  # Latency in milliseconds
    jitter_ms: float = 0.0  # Jitter in milliseconds (ookla only)
    server: Optional[str] = None  # Server name/location
    isp: Optional[str] = None  # ISP name (ookla only)
    packet_loss: float = 0.0  # Packet loss percentage (ookla only)
    result_url: Optional[str] = None  # Result URL (ookla only)

    def to_dict(self):
        """Convert to dictionary for database insertion."""
        return asdict(self)


def has_internet():
    """Check if we have internet connectivity."""
    try:
        # Connect to Google's Public DNS (8.8.8.8) on port 53
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False


def wait_for_connection(interface: str = "wlan0", timeout: int = 20) -> bool:
    """Wait for network interface to reach connected state.

    Args:
        interface: WiFi interface name
        timeout: Maximum time to wait in seconds

    Returns:
        True if connection established, False if timeout
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        res = run_cmd(
            ["nmcli", "-t", "-f", "GENERAL.STATE", "dev", "show", interface],
            timeout=5,
        )
        if res:
            state = res.stdout.strip().lower()
            # nmcli typically reports "100 (connected)" or similar
            if "connected" in state or "activated" in state:
                return True
        time.sleep(0.5)
    return False


def reconnect_saved_network(bssid: str, ssid: Optional[str] = None) -> bool:
    """Attempt to reconnect to a saved network by BSSID.

    Tries `nmcli dev wifi connect <ssid> bssid <bssid>` first when SSID is
    available, then falls back to `nmcli dev wifi connect <bssid>`.
    """
    if ssid:
        res = run_cmd(
            ["nmcli", "dev", "wifi", "connect", ssid, "bssid", bssid.upper()],
            timeout=20,
        )
        if res and res.returncode == 0:
            return True

    res = run_cmd(
        ["nmcli", "dev", "wifi", "connect", bssid.upper()],
        timeout=20,
    )
    return res is not None and res.returncode == 0


def connect_to_network(ssid: str, bssid: str, password: str) -> tuple[bool, str]:
    """Connect to a WiFi network/AP using nmcli with BSSID targeting.

    Args:
        ssid: Network SSID to connect to
        bssid: Access point BSSID (MAC address)
        password: Password for the network

    Returns:
        Tuple of (success: bool, error_message: str)
    """
    try:
        # Try to connect using nmcli
        result = subprocess.run(
            [
                "nmcli",
                "dev",
                "wifi",
                "connect",
                ssid,
                "password",
                password,
                "bssid",
                bssid.upper(),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, ""
        else:
            return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Connection timeout"
    except subprocess.CalledProcessError as e:
        return False, str(e)


def disconnect_network(interface: str) -> bool:
    """Disconnect WiFi interface using nmcli.

    Args:
        interface: WiFi interface name

    Returns:
        True if disconnection successful, False otherwise
    """
    result = run_cmd(["nmcli", "dev", "disconnect", interface], timeout=10)
    return result is not None and result.returncode == 0


def get_current_link_info(interface: str) -> tuple[Optional[str], Optional[str]]:
    """Get SSID and BSSID for the currently connected network.

    Parses output from `iw dev <interface> link`, with fallback to nmcli
    for compatibility on systems where `iw` output differs.

    Args:
        interface: WiFi interface name

    Returns:
        Tuple of (ssid, bssid), where each value is None if unavailable.
    """
    result = run_cmd(["iw", "dev", interface, "link"], timeout=5)

    ssid: Optional[str] = None
    bssid: Optional[str] = None

    if result:
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()

            if line.lower().startswith("connected to "):
                parts = line.split()
                if len(parts) >= 3:
                    candidate = parts[2].strip().lower()
                    octets = candidate.split(":")
                    if len(octets) == 6 and all(len(octet) == 2 for octet in octets):
                        bssid = candidate

            elif line.startswith("SSID:"):
                candidate = line.split(":", 1)[1].strip()
                if candidate:
                    ssid = candidate

    # Fallback to nmcli for compatibility on systems where `iw` output differs.
    if not ssid or not bssid:
        nmcli_result = run_cmd(
            ["nmcli", "-t", "-f", "active,ssid,bssid", "dev", "wifi"], timeout=5
        )
        if nmcli_result:
            for line in nmcli_result.stdout.split("\n"):
                if line.startswith("yes:"):
                    parts = line.split(":")
                    if not ssid and len(parts) >= 2:
                        ssid = parts[1] or None
                    if not bssid and len(parts) >= 3:
                        # BSSID has colons, rejoin remaining parts
                        bssid_candidate = ":".join(parts[2:]).strip().lower()
                        if bssid_candidate:
                            bssid = bssid_candidate
                    break

    return ssid, bssid


def get_current_ssid(interface: str) -> Optional[str]:
    """Get the SSID of the currently connected network.

    Args:
        interface: WiFi interface name

    Returns:
        SSID string or None if not connected
    """
    ssid, _ = get_current_link_info(interface)
    return ssid


def get_current_bssid(interface: str) -> Optional[str]:
    """Get the BSSID (AP MAC address) of the currently connected network.

    Runs `iw dev <interface> link` and parses output like:
    `Connected to 34:ca:81:49:15:ff (on wlp15s0)`

    Args:
        interface: WiFi interface name

    Returns:
        BSSID MAC address string if connected, otherwise None
    """
    _, bssid = get_current_link_info(interface)
    return bssid


def parse_ookla_json(json_output: str) -> Optional[SpeedtestResult]:
    """Parse Ookla speedtest JSON output.

    Args:
        json_output: JSON output from 'speedtest --format=json'

    Returns:
        SpeedtestResult object or None if parsing fails
    """
    try:
        data = json.loads(json_output)

        # Convert bits per second to Mbps
        download_mbps = _bits_to_mbps(
            _get_nested(data, "download", "bandwidth", default=0)
        )
        upload_mbps = _bits_to_mbps(_get_nested(data, "upload", "bandwidth", default=0))
        ping_ms = _get_nested(data, "ping", "latency", default=0) or 0
        jitter_ms = _get_nested(data, "ping", "jitter", default=0) or 0

        result = SpeedtestResult(
            tool="ookla",
            download_mbps=download_mbps,
            upload_mbps=upload_mbps,
            ping_ms=ping_ms,
            jitter_ms=jitter_ms,
            server=_get_nested(data, "server", "name"),
            isp=data.get("isp"),
            packet_loss=data.get("packetLoss", 0),
            result_url=_get_nested(data, "result", "url"),
        )

        return result
    except (json.JSONDecodeError, TypeError, KeyError):
        logging.exception("Error parsing Ookla speedtest output")
        return None


def parse_iperf3_json(json_output: str) -> Optional[SpeedtestResult]:
    """Parse iperf3 JSON output.

    Args:
        json_output: JSON output from 'iperf3 --json'

    Returns:
        SpeedtestResult object or None if parsing fails
    """
    try:
        data = json.loads(json_output)

        # Get the summary results
        summary = data.get("end", {})
        sum_sent = summary.get("sum_sent", {})
        sum_received = summary.get("sum_received", {})
        sum_overall = summary.get("sum", {})

        # iperf3 reports bits per second, convert to Mbps
        # Use received data for download (what we got), sent for upload (what we sent)
        download_mbps = _bits_to_mbps(sum_received.get("bits_per_second", 0))
        upload_mbps = _bits_to_mbps(sum_sent.get("bits_per_second", 0))

        # Extract jitter and packet loss for UDP tests when available.
        # iperf3 may include these under `sum_received`, `sum`, or `sum_sent` depending on mode.
        def _pick(*maps, key, default=0.0):
            for m in maps:
                if m and key in m and m.get(key) is not None:
                    return float(m.get(key) or 0.0)
            return float(default)

        jitter_ms = _pick(
            sum_received, sum_overall, sum_sent, key="jitter_ms", default=0.0
        )
        lost_percent = _pick(
            sum_received, sum_overall, sum_sent, key="lost_percent", default=0.0
        )

        # iperf3 doesn't report ICMP round-trip time; keep ping as 0.0
        server_host = _get_nested(
            data, "start", "connecting_to", "host", default="Unknown"
        )

        result = SpeedtestResult(
            tool="iperf3",
            download_mbps=download_mbps,
            upload_mbps=upload_mbps,
            ping_ms=0.0,
            jitter_ms=jitter_ms,
            server=server_host,
            packet_loss=lost_percent,
        )

        return result
    except (json.JSONDecodeError, TypeError, KeyError):
        logging.exception("Error parsing iperf3 output")
        return None


def run_ookla_speedtest(interface: Optional[str] = None) -> Optional[SpeedtestResult]:
    """Run Ookla speedtest and return results.

    Args:
        interface: Optional WiFi interface to test on

    Returns:
        SpeedtestResult object or None if test fails

    Raises:
        RuntimeError: If speedtest command fails
    """
    try:
        cmd = ["speedtest", "--format=json", "--accept-license"]
        if interface:
            cmd.extend(["--interface", interface])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            check=True,
        )

        return parse_ookla_json(result.stdout)

    except subprocess.TimeoutExpired:
        raise RuntimeError("Ookla speedtest timed out (exceeded 5 minutes)")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Ookla speedtest failed: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("speedtest command not found")


def run_iperf3_speedtest(
    server: str,
    port: int = 5201,
    duration: int = 10,
    udp: bool = False,
    bandwidth: str | None = None,
) -> Optional[SpeedtestResult]:
    """Run iperf3 speedtest and return results.

    Args:
        server: iperf3 server address
        port: iperf3 server port (default: 5201)
        duration: Test duration in seconds (default: 10)

    Returns:
        SpeedtestResult object or None if test fails

    Raises:
        RuntimeError: If iperf3 command fails
    """
    try:
        cmd = ["iperf3", "-c", server, "-p", str(port), "-t", str(duration), "-J"]

        if udp:
            # Use UDP mode and optionally set bandwidth
            cmd.append("-u")
            if bandwidth:
                cmd.extend(["-b", str(bandwidth)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duration + 30,  # Give extra time for setup
            check=True,
        )

        parsed = parse_iperf3_json(result.stdout)
        if parsed:
            parsed.server = server
        return parsed

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"iperf3 test timed out (exceeded {duration + 30}s)")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"iperf3 test failed: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("iperf3 command not found")
