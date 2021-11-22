import abc


class IControlChannel(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def should_agent_stop(self) -> bool:
        """
        Checks if the agent should stop
        """

    @abc.abstractmethod
    def get_config(self) -> dict:
        """
        :return: Config data (should be dict)
        """
        pass

    @abc.abstractmethod
    def get_credentials_for_propagation(self) -> dict:
        """
        :return: Credentials for propagation data (should be dict)
        """
        pass
