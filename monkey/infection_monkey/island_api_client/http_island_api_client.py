import functools
import json
import logging
from pprint import pformat
from typing import List, Sequence

import requests

from common import AgentRegistrationData, OperatingSystem
from common.agent_configuration import AgentConfiguration
from common.agent_event_serializers import AgentEventSerializerRegistry, JSONSerializable
from common.agent_events import AbstractAgentEvent
from common.common_consts.timeouts import (
    LONG_REQUEST_TIMEOUT,
    MEDIUM_REQUEST_TIMEOUT,
    SHORT_REQUEST_TIMEOUT,
)
from common.credentials import Credentials

from . import (
    AbstractIslandAPIClientFactory,
    IIslandAPIClient,
    IslandAPIConnectionError,
    IslandAPIError,
    IslandAPIRequestError,
    IslandAPIRequestFailedError,
    IslandAPITimeoutError,
)

logger = logging.getLogger(__name__)


def handle_island_errors(fn):
    @functools.wraps(fn)
    def decorated(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except requests.exceptions.ConnectionError as err:
            raise IslandAPIConnectionError(err)
        except requests.exceptions.HTTPError as err:
            if 400 <= err.response.status_code < 500:
                raise IslandAPIRequestError(err)
            elif 500 <= err.response.status_code < 600:
                raise IslandAPIRequestFailedError(err)
            else:
                raise IslandAPIError(err)
        except TimeoutError as err:
            raise IslandAPITimeoutError(err)
        except IslandAPIError as err:
            raise err
        except Exception as err:
            raise IslandAPIError(err)

    return decorated


class HTTPIslandAPIClient(IIslandAPIClient):
    """
    A client for the Island's HTTP API
    """

    def __init__(
        self,
        agent_event_serializer_registry: AgentEventSerializerRegistry,
    ):
        self._agent_event_serializer_registry = agent_event_serializer_registry

    @handle_island_errors
    def connect(
        self,
        island_server: str,
    ):
        response = requests.get(  # noqa: DUO123
            f"https://{island_server}/api?action=is-up",
            verify=False,
            timeout=MEDIUM_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        self._island_server = island_server
        self._api_url = f"https://{self._island_server}/api"

    @handle_island_errors
    def send_log(self, log_contents: str):
        response = requests.post(  # noqa: DUO123
            f"{self._api_url}/log",
            json=log_contents,
            verify=False,
            timeout=MEDIUM_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

    @handle_island_errors
    def get_pba_file(self, filename: str):
        response = requests.get(  # noqa: DUO123
            f"{self._api_url}/pba/download/{filename}",
            verify=False,
            timeout=LONG_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        return response.content

    @handle_island_errors
    def get_agent_binary(self, operating_system: OperatingSystem):
        os_name = operating_system.value
        response = requests.get(  # noqa: DUO123
            f"{self._api_url}/agent-binaries/{os_name}",
            verify=False,
            timeout=MEDIUM_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        return response.content

    @handle_island_errors
    def send_events(self, events: Sequence[JSONSerializable]):
        response = requests.post(  # noqa: DUO123
            f"{self._api_url}/agent-events",
            json=self._serialize_events(events),
            verify=False,
            timeout=MEDIUM_REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    def register_agent(self, agent_registration_data: AgentRegistrationData):
        try:
            url = f"https://{agent_registration_data.cc_server}/api/agents"
            response = requests.post(  # noqa: DUO123
                url,
                json=agent_registration_data.dict(simplify=True),
                verify=False,
                timeout=SHORT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.TooManyRedirects,
            requests.exceptions.HTTPError,
        ) as e:
            raise IslandAPIConnectionError(e)
        except requests.exceptions.Timeout as e:
            raise IslandAPITimeoutError(e)

    def should_agent_stop(self, island_server: str, agent_id: str) -> bool:
        try:
            url = f"https://{island_server}/api/monkey-control" f"/needs-to-stop/{agent_id}"
            response = requests.get(  # noqa: DUO123
                url,
                verify=False,
                timeout=SHORT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            json_response = json.loads(response.content.decode())
            return json_response["stop_agent"]
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.TooManyRedirects,
        ) as e:
            raise IslandAPIConnectionError(e)
        except requests.exceptions.Timeout as e:
            raise IslandAPITimeoutError(e)
        except requests.exceptions.HTTPError as e:
            if e.errno >= 500:
                raise IslandAPIRequestFailedError(e)
            else:
                raise IslandAPIRequestError(e)
        except json.JSONDecodeError as e:
            raise IslandAPIRequestFailedError(e)

    def get_config(self, island_server: str) -> AgentConfiguration:
        try:
            response = requests.get(  # noqa: DUO123
                f"https://{island_server}/api/agent-configuration",
                verify=False,
                timeout=SHORT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            config_dict = json.loads(response.text)

            logger.debug(f"Received configuration:\n{pformat(config_dict)}")

            return AgentConfiguration(**config_dict)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.TooManyRedirects,
        ) as e:
            raise IslandAPIConnectionError(e)
        except requests.exceptions.Timeout as e:
            raise IslandAPITimeoutError(e)
        except requests.exceptions.HTTPError as e:
            if e.errno >= 500:
                raise IslandAPIRequestFailedError(e)
            else:
                raise IslandAPIRequestError(e)
        except json.JSONDecodeError as e:
            raise IslandAPIRequestFailedError(e)

    def get_credentials_for_propagation(self, island_server: str) -> Sequence[Credentials]:
        try:
            response = requests.get(  # noqa: DUO123
                f"https://{island_server}/api/propagation-credentials",
                verify=False,
                timeout=SHORT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            return [Credentials(**credentials) for credentials in response.json()]
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.TooManyRedirects,
        ) as e:
            raise IslandAPIConnectionError(e)
        except requests.exceptions.Timeout as e:
            raise IslandAPITimeoutError(e)
        except requests.exceptions.HTTPError as e:
            if e.errno >= 500:
                raise IslandAPIRequestFailedError(e)
            else:
                raise IslandAPIRequestError(e)
        except json.JSONDecodeError as e:
            raise IslandAPIRequestFailedError(e)

    def _serialize_events(self, events: Sequence[AbstractAgentEvent]) -> JSONSerializable:
        serialized_events: List[JSONSerializable] = []

        try:
            for e in events:
                serializer = self._agent_event_serializer_registry[e.__class__]
                serialized_events.append(serializer.serialize(e))
        except Exception as err:
            raise IslandAPIRequestError(err)

        return serialized_events


class HTTPIslandAPIClientFactory(AbstractIslandAPIClientFactory):
    def __init__(
        self,
        agent_event_serializer_registry: AgentEventSerializerRegistry = None,
    ):
        self._agent_event_serializer_registry = agent_event_serializer_registry

    def create_island_api_client(self):
        return HTTPIslandAPIClient(self._agent_event_serializer_registry)
