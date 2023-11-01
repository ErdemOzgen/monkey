from __future__ import annotations

from pathlib import Path
from typing import Annotated

from monkeytypes import InfectionMonkeyBaseModel
from pydantic import BeforeValidator, Field

from common.utils.file_utils import expand_path
from monkey_island.cc.server_utils.consts import (
    DEFAULT_CRT_PATH,
    DEFAULT_DATA_DIR,
    DEFAULT_KEY_PATH,
    DEFAULT_LOG_LEVEL,
    DEFAULT_NODE_PORT,
    DEFAULT_START_MONGO_DB,
)


class MongoDBConfig(InfectionMonkeyBaseModel):
    start_mongodb: bool = DEFAULT_START_MONGO_DB


class SSLCertificatesConfig(InfectionMonkeyBaseModel):
    ssl_certificate_file: Annotated[
        Path, Field(default=Path(DEFAULT_CRT_PATH)), BeforeValidator(expand_path)
    ]
    ssl_certificate_key_file: Annotated[
        Path, Field(default=Path(DEFAULT_KEY_PATH)), BeforeValidator(expand_path)
    ]


class IslandConfigOptions(InfectionMonkeyBaseModel):
    data_dir: Annotated[Path, Field(default=DEFAULT_DATA_DIR), BeforeValidator(expand_path)]
    log_level: str = DEFAULT_LOG_LEVEL
    mongodb: MongoDBConfig = MongoDBConfig()
    ssl_certificate: SSLCertificatesConfig = SSLCertificatesConfig()
    node_port: int = DEFAULT_NODE_PORT
