import logging
import os
from http import HTTPStatus
from threading import Thread
from time import sleep

import pytest
import requests

from common.types import OTP
from envs.monkey_zoo.blackbox.analyzers.communication_analyzer import CommunicationAnalyzer
from envs.monkey_zoo.blackbox.analyzers.zerologon_analyzer import ZerologonAnalyzer
from envs.monkey_zoo.blackbox.island_client.agent_requests import AgentRequests
from envs.monkey_zoo.blackbox.island_client.i_monkey_island_requests import IMonkeyIslandRequests
from envs.monkey_zoo.blackbox.island_client.monkey_island_client import (
    GET_AGENT_EVENTS_ENDPOINT,
    GET_AGENTS_ENDPOINT,
    GET_MACHINES_ENDPOINT,
    ISLAND_LOG_ENDPOINT,
    LOGOUT_ENDPOINT,
    MonkeyIslandClient,
)
from envs.monkey_zoo.blackbox.island_client.monkey_island_requests import MonkeyIslandRequests
from envs.monkey_zoo.blackbox.island_client.reauthorizing_monkey_island_requests import (
    ReauthorizingMonkeyIslandRequests,
)
from envs.monkey_zoo.blackbox.island_client.test_configuration_parser import get_target_ips
from envs.monkey_zoo.blackbox.log_handlers.test_logs_handler import TestLogsHandler
from envs.monkey_zoo.blackbox.test_configurations import (
    credentials_reuse_ssh_key_test_configuration,
    depth_1_a_test_configuration,
    depth_2_a_test_configuration,
    depth_3_a_test_configuration,
    depth_4_a_test_configuration,
    smb_pth_test_configuration,
    wmi_mimikatz_test_configuration,
    zerologon_test_configuration,
)
from envs.monkey_zoo.blackbox.test_configurations.test_configuration import TestConfiguration
from envs.monkey_zoo.blackbox.tests.exploitation import ExploitationTest
from envs.monkey_zoo.blackbox.utils.gcp_machine_handlers import (
    initialize_gcp_client,
    start_machines,
    stop_machines,
)
from monkey_island.cc.services.authentication_service.flask_resources.agent_otp import (
    MAX_OTP_REQUESTS_PER_SECOND,
)

DEFAULT_TIMEOUT_SECONDS = 2 * 60 + 30
MACHINE_BOOTUP_WAIT_SECONDS = 30
LOG_DIR_PATH = "./logs"
logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


@pytest.fixture(autouse=True, scope="session")
def GCPHandler(request, no_gcp, gcp_machines_to_start):
    if no_gcp:
        return
    if len(gcp_machines_to_start) == 0:
        LOGGER.info("No GCP machines to start.")
    else:
        LOGGER.info(f"MACHINES TO START: {gcp_machines_to_start}")

        try:
            initialize_gcp_client()
            start_machines(gcp_machines_to_start)
        except Exception as e:
            LOGGER.error("GCP Handler failed to initialize: %s." % e)
            pytest.exit("Encountered an error while starting GCP machines. Stopping the tests.")
        wait_machine_bootup()

        def fin():
            stop_machines(gcp_machines_to_start)

        request.addfinalizer(fin)


@pytest.fixture(autouse=True, scope="session")
def delete_logs():
    LOGGER.info("Deleting monkey logs before new tests.")
    TestLogsHandler.delete_log_folder_contents(TestMonkeyBlackbox.get_log_dir_path())


def wait_machine_bootup():
    sleep(MACHINE_BOOTUP_WAIT_SECONDS)


@pytest.fixture(scope="session")
def monkey_island_requests(island) -> IMonkeyIslandRequests:
    return MonkeyIslandRequests(island)


@pytest.fixture(scope="session")
def island_client(monkey_island_requests):
    client_established = False
    try:
        reauthorizing_island_requests = ReauthorizingMonkeyIslandRequests(monkey_island_requests)
        island_client_object = MonkeyIslandClient(reauthorizing_island_requests)
        client_established = island_client_object.get_api_status()
    except Exception:
        logging.exception("Got an exception while trying to establish connection to the Island.")
    finally:
        if not client_established:
            pytest.exit("BB tests couldn't establish communication to the island.")

    yield island_client_object


@pytest.fixture(autouse=True, scope="session")
def register(island_client):
    logging.info("Registering a new user")
    island_client.register()


@pytest.mark.parametrize(
    "authenticated_endpoint",
    [
        GET_AGENTS_ENDPOINT,
        ISLAND_LOG_ENDPOINT,
        GET_MACHINES_ENDPOINT,
    ],
)
def test_logout(island, authenticated_endpoint):
    monkey_island_requests = MonkeyIslandRequests(island)
    # Prove that we can't access authenticated endpoints without logging in
    resp = monkey_island_requests.get(authenticated_endpoint)
    assert resp.status_code == HTTPStatus.UNAUTHORIZED

    # Prove that we can access authenticated endpoints after logging in
    monkey_island_requests.login()
    resp = monkey_island_requests.get(authenticated_endpoint)
    assert resp.ok

    # Log out - NOTE: This is an "out-of-band" call to logout. DO NOT call
    # `monkey_island_request.logout()`. This could allow implementation details of the
    # MonkeyIslandRequests class to cause false positives.
    monkey_island_requests.post(LOGOUT_ENDPOINT, data=None)

    # Prove that we can't access authenticated endpoints after logging out
    resp = monkey_island_requests.get(authenticated_endpoint)
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_logout_invalidates_all_tokens(island):
    monkey_island_requests_1 = MonkeyIslandRequests(island)
    monkey_island_requests_2 = MonkeyIslandRequests(island)

    monkey_island_requests_1.login()
    monkey_island_requests_2.login()

    # Prove that we can access authenticated endpoints after logging in
    resp_1 = monkey_island_requests_1.get(GET_AGENTS_ENDPOINT)
    resp_2 = monkey_island_requests_2.get(GET_AGENTS_ENDPOINT)
    assert resp_1.ok
    assert resp_2.ok

    # Log out - NOTE: This is an "out-of-band" call to logout. DO NOT call
    # `monkey_island_request.logout()`. This could allow implementation details of the
    # MonkeyIslandRequests class to cause false positives.
    # NOTE: Logout is ONLY called on monkey_island_requests_1. This is to prove that
    # monkey_island_requests_2 also gets logged out.
    monkey_island_requests_1.post(LOGOUT_ENDPOINT, data=None)

    # Prove monkey_island_requests_2 can't authenticate after monkey_island_requests_1 logs out.
    resp = monkey_island_requests_2.get(GET_AGENTS_ENDPOINT)
    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_agent_otp_rate_limit(island):
    threads = []
    response_codes = []
    agent_otp_endpoint = f"https://{island}/api/agent-otp"

    def make_request():
        response = requests.get(agent_otp_endpoint, verify=False)  # noqa: DUO123
        response_codes.append(response.status_code)

    for _ in range(0, MAX_OTP_REQUESTS_PER_SECOND + 1):
        t = Thread(target=make_request, daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    assert response_codes.count(HTTPStatus.OK) == MAX_OTP_REQUESTS_PER_SECOND
    assert response_codes.count(HTTPStatus.TOO_MANY_REQUESTS) == 1


UUID = "00000000-0000-0000-0000-000000000000"
AGENT_BINARIES_ENDPOINT = "/api/agent-binaries/os"
AGENT_EVENTS_ENDPOINT = "/api/agent-events"
AGENT_HEARTBEAT_ENDPOINT = f"/api/agent/{UUID}/heartbeat"
PUT_LOG_ENDPOINT = f"/api/agent-logs/{UUID}"
GET_AGENT_PLUGINS_ENDPOINT = "/api/agent-plugins/host/type/name"
GET_AGENT_SIGNALS_ENDPOINT = f"/api/agent-signals/{UUID}"


def test_island__cannot_access_nonisland_endpoints(island):
    island_requests = MonkeyIslandRequests(island)
    island_requests.login()

    assert island_requests.get(AGENT_BINARIES_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert (
        island_requests.post(AGENT_EVENTS_ENDPOINT, data=None).status_code == HTTPStatus.FORBIDDEN
    )
    assert (
        island_requests.post(AGENT_HEARTBEAT_ENDPOINT, data=None).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert island_requests.put(PUT_LOG_ENDPOINT, data=None).status_code == HTTPStatus.FORBIDDEN
    assert island_requests.get(GET_AGENT_PLUGINS_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert (
        island_requests.get("/api/agent-plugins/plugin-type/plugin-name/manifest").status_code
        == HTTPStatus.FORBIDDEN
    )
    assert island_requests.get(GET_AGENT_SIGNALS_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert island_requests.post(GET_AGENTS_ENDPOINT, data=None).status_code == HTTPStatus.FORBIDDEN


GET_AGENT_OTP_ENDPOINT = "/api/agent-otp"
REQUESTS_AGENT_ID = "00000000-0000-0000-0000-000000000001"
TERMINATE_AGENTS_ENDPOINT = "/api/agent-signals/terminate-all-agents"
CLEAR_SIMULATION_DATA_ENDPOINT = "/api/clear-simulation-data"
MONKEY_EXPLOITATION_ENDPOINT = "/api/exploitations/monkey"
GET_ISLAND_LOG_ENDPOINT = "/api/island/log"
ISLAND_MODE_ENDPOINT = "/api/island/mode"
ISLAND_RUN_ENDPOINT = "/api/local-monkey"
GET_NODES_ENDPOINT = "/api/nodes"
PROPAGATION_CREDENTIALS_ENDPOINT = "/api/propagation-credentials"
GET_RANSOMWARE_REPORT_ENDPOINT = "/api/report/ransomware"
REMOTE_RUN_ENDPOINT = "/api/remote-monkey"
GET_REPORT_STATUS_ENDPOINT = "/api/report-generation-status"
RESET_AGENT_CONFIG_ENDPOINT = "/api/reset-agent-configuration"
GET_SECURITY_REPORT_ENDPOINT = "/api/report/security"
GET_ISLAND_VERSION_ENDPOINT = "/api/island/version"
PUT_AGENT_CONFIG_ENDPOINT = "/api/agent-configuration"


def test_agent__cannot_access_nonagent_endpoints(island):
    island_requests = MonkeyIslandRequests(island)
    island_requests.login()
    response = island_requests.get(GET_AGENT_OTP_ENDPOINT)
    print(f"response: {response.json()}")
    otp = response.json()["otp"]

    agent_requests = AgentRequests(island, REQUESTS_AGENT_ID, OTP(otp))
    agent_requests.login()

    assert agent_requests.get(GET_AGENT_EVENTS_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(PUT_LOG_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert (
        agent_requests.post(TERMINATE_AGENTS_ENDPOINT, data=None).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert agent_requests.get(GET_AGENTS_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert (
        agent_requests.post(CLEAR_SIMULATION_DATA_ENDPOINT, data=None).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert agent_requests.get(MONKEY_EXPLOITATION_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(GET_ISLAND_LOG_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(ISLAND_MODE_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.put(ISLAND_MODE_ENDPOINT, data=None).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.post(ISLAND_RUN_ENDPOINT, data=None).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(GET_MACHINES_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(GET_NODES_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert (
        agent_requests.put(PROPAGATION_CREDENTIALS_ENDPOINT, data=None).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert agent_requests.get(GET_RANSOMWARE_REPORT_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(REMOTE_RUN_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.post(REMOTE_RUN_ENDPOINT, data=None).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(GET_REPORT_STATUS_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert (
        agent_requests.post(RESET_AGENT_CONFIG_ENDPOINT, data=None).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert agent_requests.get(GET_SECURITY_REPORT_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert agent_requests.get(GET_ISLAND_VERSION_ENDPOINT).status_code == HTTPStatus.FORBIDDEN
    assert (
        agent_requests.put(PUT_AGENT_CONFIG_ENDPOINT, data=None).status_code == HTTPStatus.FORBIDDEN
    )


# NOTE: These test methods are ordered to give time for the slower zoo machines
# to boot up and finish starting services.
# noinspection PyUnresolvedReferences
class TestMonkeyBlackbox:
    @staticmethod
    def run_exploitation_test(
        island_client: MonkeyIslandClient,
        test_configuration: TestConfiguration,
        test_name: str,
        timeout_in_seconds=DEFAULT_TIMEOUT_SECONDS,
    ):
        analyzer = CommunicationAnalyzer(
            island_client,
            get_target_ips(test_configuration),
        )
        log_handler = TestLogsHandler(
            test_name, island_client, TestMonkeyBlackbox.get_log_dir_path()
        )
        ExploitationTest(
            name=test_name,
            island_client=island_client,
            test_configuration=test_configuration,
            analyzers=[analyzer],
            timeout=timeout_in_seconds,
            log_handler=log_handler,
        ).run()

    @staticmethod
    def get_log_dir_path():
        return os.path.abspath(LOG_DIR_PATH)

    def test_credentials_reuse_ssh_key(self, island_client):
        TestMonkeyBlackbox.run_exploitation_test(
            island_client, credentials_reuse_ssh_key_test_configuration, "Credentials_Reuse_SSH_Key"
        )

    def test_depth_2_a(self, island_client):
        TestMonkeyBlackbox.run_exploitation_test(
            island_client, depth_2_a_test_configuration, "Depth2A test suite"
        )

    def test_depth_1_a(self, island_client):
        TestMonkeyBlackbox.run_exploitation_test(
            island_client, depth_1_a_test_configuration, "Depth1A test suite"
        )

    def test_depth_3_a(self, island_client):
        TestMonkeyBlackbox.run_exploitation_test(
            island_client, depth_3_a_test_configuration, "Depth3A test suite"
        )

    def test_depth_4_a(self, island_client):
        TestMonkeyBlackbox.run_exploitation_test(
            island_client, depth_4_a_test_configuration, "Depth4A test suite"
        )

    # Not grouped because it's slow
    def test_zerologon_exploiter(self, island_client):
        test_name = "Zerologon_exploiter"
        expected_creds = [
            "Administrator",
            "aad3b435b51404eeaad3b435b51404ee",
            "2864b62ea4496934a5d6e86f50b834a5",
        ]
        zero_logon_analyzer = ZerologonAnalyzer(island_client, expected_creds)
        communication_analyzer = CommunicationAnalyzer(
            island_client,
            get_target_ips(zerologon_test_configuration),
        )
        log_handler = TestLogsHandler(
            test_name, island_client, TestMonkeyBlackbox.get_log_dir_path()
        )
        ExploitationTest(
            name=test_name,
            island_client=island_client,
            test_configuration=zerologon_test_configuration,
            analyzers=[zero_logon_analyzer, communication_analyzer],
            timeout=DEFAULT_TIMEOUT_SECONDS + 30,
            log_handler=log_handler,
        ).run()

    # Not grouped because conflicts with SMB.
    # Consider grouping when more depth 1 exploiters collide with group depth_1_a
    def test_wmi_and_mimikatz_exploiters(self, island_client):
        TestMonkeyBlackbox.run_exploitation_test(
            island_client, wmi_mimikatz_test_configuration, "WMI_exploiter,_mimikatz"
        )

    # Not grouped because it's depth 1 but conflicts with SMB exploiter in group depth_1_a
    def test_smb_pth(self, island_client):
        TestMonkeyBlackbox.run_exploitation_test(
            island_client, smb_pth_test_configuration, "SMB_PTH"
        )
