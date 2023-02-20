import logging
import threading
from ipaddress import IPv4Address, IPv4Interface
from queue import Queue
from typing import List, Mapping, Sequence

from common.agent_configuration import (
    ExploitationConfiguration,
    NetworkScanConfiguration,
    PropagationConfiguration,
    ScanTargetConfiguration,
)
from common.types import Event, NetworkPort, PortStatus
from infection_monkey.i_puppet import (
    ExploiterResultData,
    FingerprintData,
    PingScanData,
    PortScanData,
    TargetHost,
)
from infection_monkey.network import NetworkAddress
from infection_monkey.network_scanning.scan_target_generator import compile_scan_target_list
from infection_monkey.utils.threading import create_daemon_thread

from . import Exploiter, IPScanner, IPScanResults
from .ip_scan_results import FingerprinterName

logger = logging.getLogger()


class Propagator:
    def __init__(
        self,
        ip_scanner: IPScanner,
        exploiter: Exploiter,
        local_network_interfaces: List[IPv4Interface],
    ):
        self._ip_scanner = ip_scanner
        self._exploiter = exploiter
        self._local_network_interfaces = local_network_interfaces
        self._hosts_to_exploit: Queue = Queue()

    def propagate(
        self,
        propagation_config: PropagationConfiguration,
        current_depth: int,
        servers: Sequence[str],
        stop: Event,
    ):
        logger.info("Attempting to propagate")

        network_scan_completed = threading.Event()
        self._hosts_to_exploit = Queue()

        network_scan = self._add_http_ports_to_fingerprinters(
            propagation_config.network_scan, propagation_config.exploitation.options.http_ports
        )

        scan_thread = create_daemon_thread(
            target=self._scan_network,
            name="PropagatorScanThread",
            args=(network_scan, stop),
        )
        exploit_thread = create_daemon_thread(
            target=self._exploit_hosts,
            name="PropagatorExploitThread",
            args=(
                propagation_config.exploitation,
                current_depth,
                servers,
                network_scan_completed,
                stop,
            ),
        )

        scan_thread.start()
        exploit_thread.start()

        scan_thread.join()
        network_scan_completed.set()

        exploit_thread.join()

        logger.info("Finished attempting to propagate")

    @staticmethod
    def _add_http_ports_to_fingerprinters(
        network_scan: NetworkScanConfiguration, http_ports: Sequence[int]
    ) -> NetworkScanConfiguration:
        # This is a hack to add http_ports to the options of fingerprinters
        # It will be reworked. See https://github.com/guardicore/monkey/issues/2136
        modified_fingerprinters = [*network_scan.fingerprinters]
        for i, fingerprinter in enumerate(modified_fingerprinters):
            if fingerprinter.name != "http":
                continue

            modified_options = fingerprinter.options.copy()
            modified_options["http_ports"] = list(http_ports)
            modified_fingerprinters[i] = fingerprinter.copy(update={"options": modified_options})

        return network_scan.copy(update={"fingerprinters": modified_fingerprinters})

    def _scan_network(self, scan_config: NetworkScanConfiguration, stop: Event):
        logger.info("Starting network scan")

        addresses_to_scan = self._compile_scan_target_list(scan_config.targets)
        self._ip_scanner.scan(addresses_to_scan, scan_config, self._process_scan_results, stop)

        logger.info("Finished network scan")

    def _compile_scan_target_list(
        self, target_config: ScanTargetConfiguration
    ) -> List[NetworkAddress]:
        ranges_to_scan = target_config.subnets
        inaccessible_subnets = target_config.inaccessible_subnets
        blocklisted_ips = target_config.blocked_ips
        scan_my_networks = target_config.scan_my_networks

        return compile_scan_target_list(
            self._local_network_interfaces,
            ranges_to_scan,
            inaccessible_subnets,
            blocklisted_ips,
            scan_my_networks,
        )

    def _process_scan_results(self, address: NetworkAddress, scan_results: IPScanResults):
        target_host = TargetHost(ip=IPv4Address(address.ip))

        Propagator._process_ping_scan_results(target_host, scan_results.ping_scan_data)
        Propagator._process_tcp_scan_results(target_host, scan_results.port_scan_data)
        Propagator._process_fingerprinter_results(target_host, scan_results.fingerprint_data)

        if IPScanner.port_scan_found_open_port(scan_results.port_scan_data):
            self._hosts_to_exploit.put(target_host)

    @staticmethod
    def _process_ping_scan_results(target_host: TargetHost, ping_scan_data: PingScanData):
        target_host.icmp = ping_scan_data.response_received
        if ping_scan_data.os is not None:
            target_host.operating_system = ping_scan_data.os

    @staticmethod
    def _process_tcp_scan_results(
        target_host: TargetHost, port_scan_data: Mapping[NetworkPort, PortScanData]
    ):
        for psd in filter(
            lambda scan_data: scan_data.status == PortStatus.OPEN, port_scan_data.values()
        ):
            target_host.services[psd.service_deprecated] = {}
            target_host.services[psd.service_deprecated]["display_name"] = "unknown(TCP)"
            target_host.services[psd.service_deprecated]["port"] = psd.port
            if psd.banner is not None:
                target_host.services[psd.service_deprecated]["banner"] = psd.banner

    @staticmethod
    def _process_fingerprinter_results(
        target_host: TargetHost, fingerprint_data: Mapping[FingerprinterName, FingerprintData]
    ):
        for fd in fingerprint_data.values():
            # TODO: This logic preserves the existing behavior prior to introducing IMaster and
            #       IPuppet, but it is possibly flawed. Different fingerprinters may detect
            #       different os types or versions, and this logic isn't sufficient to handle those
            #       conflicts. Reevaluate this logic when we overhaul our scanners/fingerprinters.
            if fd.os_type is not None:
                target_host.operating_system = fd.os_type

            for service, details in fd.services.items():
                target_host.services.setdefault(service, {}).update(details)

    def _exploit_hosts(
        self,
        exploitation_config: ExploitationConfiguration,
        current_depth: int,
        servers: Sequence[str],
        network_scan_completed: threading.Event,
        stop: Event,
    ):
        logger.info("Exploiting victims")

        self._exploiter.exploit_hosts(
            exploitation_config,
            self._hosts_to_exploit,
            current_depth,
            servers,
            self._process_exploit_attempts,
            network_scan_completed,
            stop,
        )

        logger.info("Finished exploiting victims")

    def _process_exploit_attempts(
        self, exploiter_name: str, host: TargetHost, result: ExploiterResultData
    ):
        if result.propagation_success:
            logger.info(f"Successfully propagated to {host} using {exploiter_name}")
        elif result.exploitation_success:
            logger.info(
                f"Successfully exploited (but did not propagate to) {host} using {exploiter_name}"
            )
        else:
            logger.info(
                f"Failed to exploit or propagate to {host} using {exploiter_name}: "
                f"{result.error_message}"
            )
