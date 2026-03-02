"""CLI interface for wifi-test."""

import os
import pwd
import shutil
import time

import click
from rich.console import Console
from rich.table import Table

from wifi_test import db
from wifi_test.config import detect_wifi_interface, get_config
from wifi_test.scanner import scan_wifi
from wifi_test.speedtest import (
    SpeedtestResult,
    connect_to_network,
    disconnect_network,
    get_current_bssid,
    get_current_ssid,
    has_internet,
    reconnect_saved_network,
    run_iperf3_speedtest,
    run_ookla_speedtest,
    wait_for_connection,
)
from wifi_test.utils import verify_iperf3_available, verify_speedtest_available

console = Console()


def _run_speedtest_for_tool(
    tool: str, cfg, interface: str, udp: bool = False, bandwidth: str | None = None
) -> SpeedtestResult:
    """Run speedtest for the configured tool.

    Args:
        tool: 'speedtest' or 'iperf3'
        cfg: Config object
        interface: WiFi interface name

    Returns:
        SpeedtestResult object or None

    Raises:
        RuntimeError: If speedtest fails
    """
    if tool == "iperf3":
        server = cfg.iperf3_server
        if not server:
            raise RuntimeError("IPERF3_SERVER not configured")

        ports = cfg.get_iperf3_ports()
        last_error = None

        for port in ports:
            try:
                bw = bandwidth if bandwidth is not None else cfg.iperf3_bandwidth
                result = run_iperf3_speedtest(server, port, udp=udp, bandwidth=bw)
                if result:
                    return result
            except RuntimeError as e:
                last_error = e
                continue

        raise last_error or RuntimeError("All iperf3 ports failed")
    else:
        return run_ookla_speedtest(interface)


def _display_multi_network_summary(results: list, detailed: bool = False):
    """Display summary table of multiple network tests.

    Args:
        results: List of test result dictionaries
        detailed: When True, include scan fields in the table
    """
    console.print("\n[bold]Test Summary:[/bold]")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("SSID", style="cyan", no_wrap=True)
    if detailed:
        table.add_column("BSSID", style="dim")
        table.add_column("Band", justify="center")
        table.add_column("Channel", justify="center")
        table.add_column("Security")
    table.add_column("Status", justify="center")
    table.add_column("Signal", justify="right")
    table.add_column("Download", justify="right")
    table.add_column("Upload", justify="right")
    if detailed:
        table.add_column("Ping", justify="right")
        table.add_column("Jitter", justify="right")
        table.add_column("Loss", justify="right")
        table.add_column("Server")

    for r in results:
        status = r["status"]
        ssid = r["ssid"]
        signal = f"{r['signal']:.1f} dBm"
        bssid = r.get("bssid", "-")
        band = r.get("band", "-")
        channel = r.get("channel", "-")
        security = r.get("security", "-")

        if status == "success":
            result = r["result"]
            row = [
                ssid,
            ]
            if detailed:
                row.extend([bssid, band, str(channel), security])
            row.extend(
                [
                    "[green]✓[/green]",
                    signal,
                    f"[green]{result.download_mbps:.2f} Mbps[/green]",
                    f"[blue]{result.upload_mbps:.2f} Mbps[/blue]",
                ]
            )
            if detailed:
                row.extend(
                    [
                        f"{result.ping_ms:.2f} ms" if result.ping_ms else "-",
                        f"{result.jitter_ms:.2f} ms" if result.jitter_ms else "-",
                        f"{result.packet_loss:.2f}%"
                        if result.packet_loss is not None
                        else "-",
                        result.server or "-",
                    ]
                )
            table.add_row(*row)
        else:
            row = [ssid]
            if detailed:
                row.extend([bssid, band, str(channel), security])
            status_text = {
                "connection_failed": "[red]Connection Failed[/red]",
                "no_internet": "[yellow]No Internet[/yellow]",
                "test_failed": "[red]Test Failed[/red]",
                "error": "[red]Error[/red]",
            }.get(status, status)
            row.extend([status_text, signal, "-", "-"])
            if detailed:
                row.extend(["-", "-", "-", "-"])
            table.add_row(*row)

    console.print(table)

    # Print statistics
    total = len(results)
    successful = sum(1 for r in results if r["status"] == "success")
    console.print(
        f"\n[bold]Results:[/bold] {successful}/{total} networks tested successfully"
    )


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """WiFi testing and monitoring utility.

    A tool for scanning WiFi networks, running speed tests, and exporting data.
    """
    if os.geteuid() != 0:
        click.echo(
            "Error: This tool must be run with sudo (root) privileges.", err=True
        )
        raise click.exceptions.Exit(1)

    # Check for required iw command
    if not shutil.which("iw"):
        click.echo(
            "Error: 'iw' command not found. Please install it:\n"
            "  Ubuntu/Debian: sudo apt install iw\n"
            "  Fedora: sudo dnf install iw",
            err=True,
        )
        raise click.exceptions.Exit(1)


@cli.group()
def config():
    """Manage configuration settings."""
    pass


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a configuration value.

    KEY: Configuration key to set (case-insensitive)
    VALUE: Value to set

    Examples:
        wifi-test config set db_path /path/to/database.db
        wifi-test config set speedtest_tool iperf3
        wifi-test config set iperf3_server 192.168.1.100
    """
    cfg = get_config()
    key_upper = key.upper()
    try:
        cfg.set(key_upper, value)
        click.echo(f"✓ Set {key_upper}={value}")
    except ValueError as e:
        click.echo(str(e), err=True)
        # Try to show supported keys if available
        try:
            supported = sorted(cfg._supported_keys)
            click.echo("Supported keys: " + ", ".join(supported), err=True)
        except Exception:
            pass
        raise click.exceptions.Exit(1)


@config.command("get")
@click.argument("key", required=False)
def config_get(key: str):
    """Get configuration value(s).

    KEY: Configuration key to get (case-insensitive, optional - shows all if omitted)

    Examples:
        wifi-test config get db_path
        wifi-test config get
    """
    cfg = get_config()

    if key:
        key_upper = key.upper()
        value = cfg.get(key_upper)
        if value is not None:
            click.echo(f"{key_upper}={value}")
        else:
            click.echo(f"Configuration key '{key_upper}' not found", err=True)
            raise click.exceptions.Exit(1)
    else:
        # Show all configuration
        config_dict = cfg.get_all()
        if config_dict:
            click.echo("Current configuration:")
            for k, v in sorted(config_dict.items()):
                click.echo(f"  {k}={v}")
        else:
            click.echo("No configuration set")


@config.command("init")
def config_init():
    """Initialize configuration with default values."""
    cfg = get_config()

    # Auto-detect WiFi interface
    detected_interface = detect_wifi_interface()

    defaults = {
        "DB_PATH": "wifi_data.db",
        "WIFI_INTERFACE": detected_interface or "wlan0",
        "SPEEDTEST_TOOL": "speedtest",
        "SCAN_FLUSH": "True",
        "OUTPUT_DIR": ".",
    }

    click.echo("Initializing configuration with defaults...")
    for key, value in defaults.items():
        if cfg.get(key) is None:
            cfg.set(key, value)
            if key == "WIFI_INTERFACE" and detected_interface:
                click.echo(f"  Set {key}={value} (auto-detected)")
            else:
                click.echo(f"  Set {key}={value}")
        else:
            click.echo(f"  Skipped {key} (already set)")

    click.echo("✓ Configuration initialized")


@config.command("detect-interface")
def config_detect_interface():
    """Detect and set active WiFi interface.

    Uses 'iw dev' to find the active WiFi interface and saves it to config.
    """
    click.echo("Detecting WiFi interface...")
    interface = detect_wifi_interface()

    if interface:
        cfg = get_config()
        cfg.set("WIFI_INTERFACE", interface)
        click.echo(f"✓ Detected and set interface: {interface}")
    else:
        click.echo(
            "✗ Could not detect WiFi interface. Please set it manually.", err=True
        )
        click.echo("  Example: wifi-test config set wifi_interface wlan0", err=True)
        raise click.exceptions.Exit(1)


@cli.command()
@click.option(
    "--prefix",
    "-p",
    "prefix_arg",
    default=None,
    is_flag=False,
    flag_value="CONFIG",
    type=str,
    help="Filter by prefix. Use -p alone to use config prefix, -p PREFIX for custom prefix",
)
@click.option("--save/--no-save", default=None, help="Override auto-save setting")
@click.option("--quiet", "-q", is_flag=True, help="Suppress all output")
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Maximum number of networks to show (strongest first)",
)
def scan(prefix_arg: str, save: bool, quiet: bool, limit: int | None):
    """Scan for WiFi networks and save results to database."""
    cfg = get_config()
    interface = cfg.wifi_interface
    flush_cache = cfg.scan_flush

    # Use config default if --save/--no-save not specified
    if save is None:
        save = cfg.auto_save

    # Handle prefix logic:
    # prefix_arg=None: no -p flag, don't filter
    # prefix_arg="CONFIG": -p flag alone, use config prefix
    # prefix_arg="VALUE": -p VALUE, use provided value
    prefix = None
    if prefix_arg == "CONFIG":
        prefix = cfg.prefix
    elif prefix_arg:
        prefix = prefix_arg

    try:
        if quiet:
            # Silent mode - no output at all
            results = scan_wifi(interface, flush_cache)
        else:
            # Normal mode with spinner
            with console.status(
                "[bold cyan]Scanning for WiFi networks...", spinner="dots"
            ) as status:
                results = scan_wifi(interface, flush_cache)

                # Filter by prefix if specified
                if prefix:
                    results = [r for r in results if r.ssid.startswith(prefix)]
                    count = len(results)
                else:
                    count = len(results)

                # Sort strongest first and apply limit if provided
                results.sort(key=lambda r: r.signal, reverse=True)
                shown = count
                if limit is not None and limit > 0:
                    results = results[:limit]
                    shown = len(results)

                if prefix:
                    if limit:
                        status.update(
                            f"[bold green]Found {count} networks matching '{prefix}' (showing top {shown})"
                        )
                    else:
                        status.update(
                            f"[bold green]Found {count} networks matching '{prefix}'"
                        )
                else:
                    if limit:
                        status.update(
                            f"[bold green]Found {count} networks (showing top {shown})"
                        )
                    else:
                        status.update(f"[bold green]Found {count} networks")

        # Filter by prefix if in quiet mode
        if quiet and prefix:
            results = [r for r in results if r.ssid.startswith(prefix)]

        # In quiet mode, also sort and apply limit
        if quiet:
            results.sort(key=lambda r: r.signal, reverse=True)
            if limit is not None and limit > 0:
                results = results[:limit]

        if not quiet:
            if not results:
                console.print("[yellow]No networks found[/yellow]")
                return

            # Display results in a nice table
            table = Table(
                title="WiFi Scan Results", show_header=True, header_style="bold magenta"
            )
            table.add_column("SSID", style="cyan", no_wrap=True)
            table.add_column("BSSID", style="dim")
            table.add_column("Band", justify="center")
            table.add_column("Channel", justify="center")
            table.add_column("Signal", justify="right")
            table.add_column("Security", style="yellow")

            # Already sorted above

            for result in results:
                # Color code signal strength
                signal_str = f"{result.signal:.1f} dBm"
                if result.signal >= -50:
                    signal_color = "green"
                elif result.signal >= -70:
                    signal_color = "yellow"
                else:
                    signal_color = "red"

                table.add_row(
                    result.ssid,
                    result.bssid,
                    result.band,
                    str(result.channel) if result.channel else "?",
                    f"[{signal_color}]{signal_str}[/{signal_color}]",
                    result.security or "Open",
                )

            console.print(table)

            if save:
                count = db.insert_scan_results([r.to_dict() for r in results])
                console.print(f"[dim]Saved {count} scan results to database[/dim]")

    except RuntimeError as e:
        if not quiet:
            console.print(f"[red]Error:[/red] {e}")
        raise click.exceptions.Exit(1)


@cli.command()
@click.option("--quiet", "-q", is_flag=True, help="Suppress all output")
@click.option(
    "--auto-connect",
    "-a",
    is_flag=True,
    help="Auto-connect to networks with prefix and test each one",
)
@click.option(
    "--prefix",
    "-p",
    "prefix_arg",
    default=None,
    type=str,
    help="Network prefix to filter (defaults to config PREFIX if not specified)",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Maximum number of networks to test in auto-connect mode",
)
@click.option(
    "--details/--no-details",
    default=False,
    help="Show full table with scan details + speedtest metrics",
)
@click.option(
    "--udp", "-u", is_flag=True, default=False, help="Use UDP for iperf3 tests"
)
def speedtest(
    quiet: bool,
    auto_connect: bool,
    prefix_arg: str,
    limit: int | None,
    details: bool,
    udp: bool,
):
    """Run speed test on current network connection or multiple networks.

    Without --auto-connect: Tests the current connection.

    With --auto-connect: Scans for networks matching prefix, connects to each,
    verifies internet connectivity, and runs speedtest.
    """
    cfg = get_config()
    tool = cfg.speedtest_tool
    interface = cfg.wifi_interface

    # Display names for user-facing output
    tool_display_names = {"speedtest": "Ookla Speedtest", "iperf3": "iperf3"}
    display_name = tool_display_names.get(tool, tool)

    # Check if the selected tool is available
    if tool == "iperf3":
        available, error_msg = verify_iperf3_available()
        if not available:
            if not quiet:
                console.print(f"[red]{error_msg}[/red]")
            raise click.exceptions.Exit(1)
    elif tool == "speedtest":
        available, error_msg = verify_speedtest_available()
        if not available:
            if not quiet:
                console.print(f"[red]{error_msg}[/red]")
            raise click.exceptions.Exit(1)
    else:
        if not quiet:
            console.print(f"[red]Error:[/red] Unknown speedtest tool '{tool}'")
            console.print("Valid options: speedtest, iperf3")
        raise click.exceptions.Exit(1)

    # AUTO-CONNECT MODE: Scan and test multiple networks
    if auto_connect:
        # Handle prefix logic: use provided prefix or fall back to config
        prefix = prefix_arg if prefix_arg else cfg.prefix

        if not prefix:
            if not quiet:
                console.print(
                    "[red]Error:[/red] --auto-connect requires a prefix filter"
                )
                console.print("Set one with: wifi-test config set prefix <PREFIX>")
                console.print("Or use: wifi-test speedtest --auto-connect -p <PREFIX>")
            raise click.exceptions.Exit(1)

        password = cfg.golden_config_password
        if not password:
            if not quiet:
                console.print("[red]Error:[/red] GOLDEN_CONFIG_PASSWORD not configured")
                console.print(
                    "Set with: wifi-test config set golden_config_password <password>"
                )
            raise click.exceptions.Exit(1)

        if not quiet:
            console.print(
                f"[bold cyan]Auto-connect mode:[/bold cyan] Testing networks with prefix '{prefix}'"
            )

        # Step 1: Scan for networks
        try:
            if not quiet:
                with console.status(
                    "[bold cyan]Scanning for WiFi networks...", spinner="dots"
                ):
                    networks = scan_wifi(interface, cfg.scan_flush)
            else:
                networks = scan_wifi(interface, cfg.scan_flush)

            # Filter by prefix
            networks = [n for n in networks if n.ssid.startswith(prefix)]

            # Sort strongest signal first
            networks.sort(key=lambda n: n.signal, reverse=True)

            # Apply limit if provided
            if limit is not None and limit > 0:
                networks = networks[:limit]

            if not networks:
                if not quiet:
                    console.print(
                        f"[yellow]No networks found with prefix '{prefix}'[/yellow]"
                    )
                return

            if not quiet:
                console.print(f"[green]Found {len(networks)} matching networks[/green]")

            # Save network results used for auto-connect (if configured)
            if cfg.auto_save:
                try:
                    count = db.insert_scan_results([n.to_dict() for n in networks])
                    if not quiet:
                        console.print(
                            f"[dim]Saved {count} scan results to database[/dim]"
                        )
                except Exception as e:
                    if not quiet:
                        console.print(f"[yellow]Warning:[/yellow] DB save failed: {e}")

        except Exception as e:
            if not quiet:
                console.print(f"[red]Scan error:[/red] {e}")
            raise click.exceptions.Exit(1)

        # Step 2: Remember current connection to restore later
        original_ssid = get_current_ssid(interface)
        original_bssid = get_current_bssid(interface)

        # Step 3: Test each network
        results = []
        for i, network in enumerate(networks, 1):
            ssid = network.ssid
            bssid = network.bssid

            if not quiet:
                console.print(
                    f"\n[bold]Testing network {i}/{len(networks)}:[/bold] {ssid}"
                )

            # Reset interface before connecting
            disconnect_network(interface)
            time.sleep(0.5)

            # Connect to network
            if not quiet:
                with console.status(
                    f"[cyan]Connecting to {ssid}...", spinner="dots"
                ) as status:
                    connected, error_msg = connect_to_network(ssid, bssid, password)
                    if connected:
                        status.update("[cyan]Waiting for connection to establish...")
                        connected = wait_for_connection(interface, timeout=25)
                        if not connected:
                            error_msg = "Connection timeout - interface not activated"
            else:
                connected, error_msg = connect_to_network(ssid, bssid, password)
                if connected:
                    connected = wait_for_connection(interface, timeout=25)
                    if not connected:
                        error_msg = "Connection timeout"

            if not connected:
                if not quiet:
                    console.print(f"[red]✗ Failed to connect to {ssid}[/red]")
                    if error_msg:
                        console.print(f"[dim]  Error: {error_msg}[/dim]")
                results.append(
                    {
                        "ssid": ssid,
                        "status": "connection_failed",
                        "signal": network.signal,
                        "bssid": network.bssid,
                        "band": network.band,
                        "channel": network.channel,
                        "security": network.security,
                        "error": error_msg,
                    }
                )
                disconnect_network(interface)
                continue

            if not quiet:
                console.print(
                    f"[green]✓ Connected to {ssid}[/green][pale_turquoise4] ({bssid})[/pale_turquoise4]"
                )

            # Wait a moment for connectivity to stabilize
            time.sleep(2)

            # Check internet connectivity
            if not has_internet():
                if not quiet:
                    console.print(f"[yellow]⚠ No internet on {ssid}[/yellow]")
                results.append(
                    {
                        "ssid": ssid,
                        "status": "no_internet",
                        "signal": network.signal,
                        "bssid": network.bssid,
                        "band": network.band,
                        "channel": network.channel,
                        "security": network.security,
                    }
                )
                disconnect_network(interface)
                continue

            # Run speedtest
            try:
                if not quiet:
                    with console.status(
                        f"[cyan]Running {display_name}...", spinner="dots"
                    ):
                        result = _run_speedtest_for_tool(tool, cfg, interface, udp=udp)
                else:
                    result = _run_speedtest_for_tool(tool, cfg, interface, udp=udp)

                if result:
                    result.ssid = ssid
                    result.bssid = get_current_bssid(interface) or network.bssid
                    results.append(
                        {
                            "ssid": ssid,
                            "status": "success",
                            "signal": network.signal,
                            "bssid": network.bssid,
                            "band": network.band,
                            "channel": network.channel,
                            "security": network.security,
                            "result": result,
                        }
                    )
                    # Save to database if enabled
                    if cfg.auto_save:
                        try:
                            db.insert_speedtest_result(result.to_dict())
                            if not quiet:
                                console.print(
                                    "[dim]Saved speedtest result to database[/dim]"
                                )
                        except Exception as e:
                            if not quiet:
                                console.print(
                                    f"[yellow]Warning:[/yellow] DB save failed: {e}"
                                )
                    if not quiet:
                        console.print(
                            f"[green]✓ {ssid}:[/green] ↓{result.download_mbps:.2f} Mbps / ↑{result.upload_mbps:.2f} Mbps"
                        )
                else:
                    results.append(
                        {
                            "ssid": ssid,
                            "status": "test_failed",
                            "signal": network.signal,
                            "bssid": network.bssid,
                            "band": network.band,
                            "channel": network.channel,
                            "security": network.security,
                        }
                    )
                    if not quiet:
                        console.print(f"[red]✗ Speedtest failed on {ssid}[/red]")

            except Exception as e:
                results.append(
                    {
                        "ssid": ssid,
                        "status": "error",
                        "signal": network.signal,
                        "bssid": network.bssid,
                        "band": network.band,
                        "channel": network.channel,
                        "security": network.security,
                        "error": str(e),
                    }
                )
                if not quiet:
                    console.print(f"[red]✗ Error on {ssid}:[/red] {e}")

            # Disconnect from this network
            disconnect_network(interface)
            time.sleep(1)

        # Step 4: Restore original connection if needed
        if original_bssid:
            if not quiet:
                console.print(
                    f"\n[dim]Restoring connection to {original_ssid or original_bssid}...[/dim]"
                )
            # Try to reconnect using saved profile or cached credentials
            reconnect_saved_network(original_bssid, original_ssid)
            time.sleep(2)

        # Step 5: Display summary
        if not quiet:
            _display_multi_network_summary(results, details)

            # Inform where results were saved when auto-save is enabled
            if cfg.auto_save:
                db_path = cfg.db_path
                console.print(f"\n[dim]Results saved to database: {db_path}[/dim]")

        return

    # SINGLE NETWORK MODE: Test current connection
    try:
        if not quiet:
            with console.status(
                f"[bold cyan]Running {display_name}...", spinner="dots"
            ) as status:
                # Run the appropriate speedtest
                if tool == "iperf3":
                    server = cfg.iperf3_server
                    if not server:
                        console.print("[red]Error:[/red] IPERF3_SERVER not configured")
                        console.print(
                            "Set with: wifi-test config set iperf3_server <server>"
                        )
                        raise click.exceptions.Exit(1)

                    ports = cfg.get_iperf3_ports()
                    result = None
                    last_error = None

                    # Try each port in the range
                    for port in ports:
                        try:
                            status.update(f"[bold cyan]Trying iperf3 on port {port}...")
                            result = run_iperf3_speedtest(
                                server, port, udp=udp, bandwidth=cfg.iperf3_bandwidth
                            )
                            if result:
                                break
                        except RuntimeError as e:
                            last_error = e
                            continue

                    if not result:
                        raise last_error or RuntimeError("All iperf3 ports failed")
                else:
                    result = run_ookla_speedtest(interface)

                status.update("[bold green]Speedtest complete!")
        else:
            # Quiet mode - no output
            if tool == "iperf3":
                server = cfg.iperf3_server
                if not server:
                    raise click.exceptions.Exit(1)

                ports = cfg.get_iperf3_ports()
                result = None

                for port in ports:
                    try:
                        result = run_iperf3_speedtest(
                            server, port, udp=udp, bandwidth=cfg.iperf3_bandwidth
                        )
                        if result:
                            break
                    except RuntimeError:
                        continue

                if not result:
                    raise click.exceptions.Exit(1)
            else:
                result = run_ookla_speedtest(interface)

        if not result:
            if not quiet:
                console.print("[red]Error:[/red] Speedtest failed")
            raise click.exceptions.Exit(1)

        current_ssid = get_current_ssid(interface)
        current_bssid = get_current_bssid(interface)
        if current_ssid:
            result.ssid = current_ssid
        if current_bssid:
            result.bssid = current_bssid

        if not quiet:
            # Display results in a nice format
            console.print("\n[bold]Speedtest Results:[/bold]")
            console.print(f"  Tool: {result.tool}")
            console.print(f"  Server: {result.server or 'Unknown'}")
            console.print(f"  Download: [green]{result.download_mbps:.2f} Mbps[/green]")
            console.print(f"  Upload: [blue]{result.upload_mbps:.2f} Mbps[/blue]")

            if result.ping_ms > 0:
                console.print(f"  Ping: [yellow]{result.ping_ms:.2f} ms[/yellow]")
            if result.jitter_ms > 0:
                console.print(f"  Jitter: {result.jitter_ms:.2f} ms")
            if result.isp:
                console.print(f"  ISP: {result.isp}")
            if result.result_url:
                console.print(f"  URL: {result.result_url}")

            # Save to database if enabled
            if cfg.auto_save:
                try:
                    db.insert_speedtest_result(result.to_dict())
                    console.print("\n[dim]Saved speedtest result to database[/dim]")
                except Exception as e:
                    console.print(f"\n[yellow]Warning:[/yellow] DB save failed: {e}")

    except RuntimeError as e:
        if not quiet:
            console.print(f"[red]Error:[/red] {e}")
        raise click.exceptions.Exit(1)


@cli.command()
@click.option(
    "--output", "-o", default=None, help="Output CSV file (will prompt if omitted)"
)
def export(output: str):
    """Export data from database to CSV file.

    If `--output` is omitted you'll be prompted for a filename.
    Exports unified `network_results` rows into a single CSV.
    `bssid` serves as the unique lookup key for network-level records.
    """
    # Prompt for filename if not provided
    if not output:
        output = click.prompt("Output CSV file", default="wifi_export.csv")

    out_path = os.path.expanduser(output)
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    # Check overwrite
    if os.path.exists(out_path):
        if not click.confirm(f"{out_path} exists — overwrite?", default=False):
            click.echo("Aborted")
            raise click.exceptions.Exit(1)

    # Fetch data
    rows = db.get_all_results()

    # Combine columns
    import csv

    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())

    if not rows:
        click.echo("No data to export")
        return

    # Ensure deterministic column order with bssid first for lookup workflows
    all_keys.discard("bssid")
    fieldnames = [
        "bssid",
        "ssid",
        "band",
        "signal",
        "security",
        "channel",
        "frequency",
        "download_mbps",
        "upload_mbps",
        "ping_ms",
        "jitter_ms",
        "packet_loss",
        "server",
        "isp",
        "result_url",
        "created_at",
    ]

    try:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in fieldnames})

        # Change ownership from root to original user if running via sudo
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            user_info = pwd.getpwnam(sudo_user)
            os.chown(out_path, user_info.pw_uid, user_info.pw_gid)

        click.echo(f"✓ Exported {len(rows)} rows to {out_path}")
    except Exception as e:
        click.echo(f"Error exporting data: {e}", err=True)
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    cli()
