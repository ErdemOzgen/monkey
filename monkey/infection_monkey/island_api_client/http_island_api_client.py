import logging
from pprint import pformat
from typing import List, Sequence

from common import AgentRegistrationData, AgentSignals, OperatingSystem
from common.agent_configuration import AgentConfiguration
from common.agent_event_serializers import AgentEventSerializerRegistry
from common.agent_events import AbstractAgentEvent
from common.common_consts.timeouts import MEDIUM_REQUEST_TIMEOUT, SHORT_REQUEST_TIMEOUT
from common.credentials import Credentials
from common.types import AgentID, JSONSerializable, PluginType, SocketAddress

from . import AbstractIslandAPIClientFactory, IIslandAPIClient, IslandAPIRequestError
from .http_requests_facade import (
    HTTPRequestsFacade,
    convert_json_error_to_island_api_error,
    handle_island_errors,
)

logger = logging.getLogger(__name__)


class HTTPIslandAPIClient(IIslandAPIClient):
    """
    A client for the Island's HTTP API
    """

    def __init__(
        self,
        agent_event_serializer_registry: AgentEventSerializerRegistry,
    ):
        self._agent_event_serializer_registry = agent_event_serializer_registry
        self.request_facade = HTTPRequestsFacade("")

    @handle_island_errors
    def connect(
        self,
        island_server: SocketAddress,
    ):
        api_url = f"https://{island_server}/api"
        requests_facade = HTTPRequestsFacade(api_url)
        requests_facade.get(  # noqa: DUO123 type: ignore [attr-defined]
            endpoint="",
            params={"action": "is-up"},
        )
        self.request_facade = requests_facade

    @handle_island_errors
    def send_log(self, agent_id: AgentID, log_contents: str):
        self.request_facade.put(
            f"agent-logs/{agent_id}",
            MEDIUM_REQUEST_TIMEOUT,
            log_contents,
        )

    @handle_island_errors
    def get_agent_binary(self, operating_system: OperatingSystem) -> bytes:
        os_name = operating_system.value
        response = self.request_facade.get(f"agent-binaries/{os_name}", MEDIUM_REQUEST_TIMEOUT)
        return response.content

    @handle_island_errors
    def send_events(self, events: Sequence[AbstractAgentEvent]):
        self.request_facade.post(
            "agent-events", MEDIUM_REQUEST_TIMEOUT, self._serialize_events(events)
        )

    @handle_island_errors
    def register_agent(self, agent_registration_data: AgentRegistrationData):
        self.request_facade.post(
            "agents",
            SHORT_REQUEST_TIMEOUT,
            agent_registration_data.dict(simplify=True),
        )

    @handle_island_errors
    @convert_json_error_to_island_api_error
    def get_config(self) -> AgentConfiguration:
        response = self.request_facade.get("agent-configuration", SHORT_REQUEST_TIMEOUT)

        config_dict = response.json()
        logger.debug(f"Received configuration:\n{pformat(config_dict)}")

        return AgentConfiguration(**config_dict)

    @handle_island_errors
    @convert_json_error_to_island_api_error
    def get_credentials_for_propagation(self) -> Sequence[Credentials]:
        response = self.request_facade.get("propagation-credentials", SHORT_REQUEST_TIMEOUT)

        return [Credentials(**credentials) for credentials in response.json()]

    def _serialize_events(self, events: Sequence[AbstractAgentEvent]) -> JSONSerializable:
        serialized_events: List[JSONSerializable] = []

        try:
            for e in events:
                serializer = self._agent_event_serializer_registry[e.__class__]
                serialized_events.append(serializer.serialize(e))
        except Exception as err:
            raise IslandAPIRequestError(err)

        return serialized_events

    @handle_island_errors
    @convert_json_error_to_island_api_error
    def get_agent_signals(self, agent_id: str) -> AgentSignals:
        response = self.request_facade.get(f"agent-signals/{agent_id}", SHORT_REQUEST_TIMEOUT)

        return AgentSignals(**response.json())

    @handle_island_errors
    def get_agent_plugin(self, plugin_type: PluginType, plugin_name: str) -> bytes:
        response = self.request_facade.get(
            f"/api/agent-plugins/{plugin_type.value}/{plugin_name}", MEDIUM_REQUEST_TIMEOUT
        )

        return response.content


class HTTPIslandAPIClientFactory(AbstractIslandAPIClientFactory):
    def __init__(
        self,
        agent_event_serializer_registry: AgentEventSerializerRegistry,
    ):
        self._agent_event_serializer_registry = agent_event_serializer_registry

    def create_island_api_client(self) -> IIslandAPIClient:
        return HTTPIslandAPIClient(self._agent_event_serializer_registry)
