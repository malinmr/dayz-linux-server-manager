import json
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict


CONFIG_DIR = Path.home() / ".config" / "dayz-server-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    # ========================================================
    # SSH CONNECTION
    # ========================================================

    host: str = ""
    port: int = 22
    username: str = ""
    key_path: str = str(
        Path.home() / ".ssh" / "id_rsa"
    )

    # ========================================================
    # SYSTEMD
    # ========================================================

    systemd_service: str = "dayz-server"

    # ========================================================
    # REMOTE PATHS
    # ========================================================
    #
    # server_root is the authoritative DayZ installation
    # directory.
    #
    # Deploy uses this value as:
    #
    #     +force_install_dir <server_root>
    #
    # Other server-owned paths are derived from this root.

    server_root: str = "/home/dayz/server"

    # The exact value used by DayZ's:
    #
    #     -profiles=<value>
    #
    # This is intentionally "profiles" on a fresh
    # configuration.
    #
    # SystemdPanel updates this value when the actual
    # ExecStart parameter is loaded or edited.

    profiles_arg: str = "profiles"

    # Resolved profiles directory used by the Files panel.
    #
    # This is derived from:
    #
    #     server_root + profiles_arg
    #
    # when profiles_arg is relative.
    #
    # An absolute profiles_arg is used directly.

    profiles_dir: str = (
        "/home/dayz/server/profiles"
    )

    # Server-root-derived directories.

    keys_dir: str = (
        "/home/dayz/server/keys"
    )

    mpmissions_dir: str = (
        "/home/dayz/server/mpmissions"
    )

    # Log directory follows the resolved profiles directory.

    log_dir: str = (
        "/home/dayz/server/profiles"
    )

    # ========================================================
    # STEAM / WORKSHOP
    # ========================================================
    #
    # SteamCMD itself may live somewhere completely different
    # from the DayZ server installation.
    #
    # Workshop content for this server is therefore derived
    # from server_root rather than steamcmd_path.

    steamcmd_path: str = (
        "/home/dayz/steamcmd/steamcmd.sh"
    )

    steam_user: str = "anonymous"

    workshop_content_dir: str = (
        "/home/dayz/server/"
        "steamapps/workshop/content/221100"
    )

    # ========================================================
    # BATTLEMETRICS API
    # ========================================================

    battlemetrics_api_key: str = ""

    battlemetrics_server_id: str = ""

    # ========================================================
    # SERVER LAUNCH CONFIG
    # ========================================================

    server_launch_config_path: str = ""

    # ========================================================
    # STEAM WEB API
    # ========================================================

    steam_api_key: str = ""

    # ========================================================
    # BATTLEYE RCON
    # ========================================================

    rcon_host: str = ""

    rcon_port: int = 2302

    rcon_password: str = ""

    rcon_auto_reconnect: bool = True

    # ========================================================
    # INSTALLED MODS
    # ========================================================

    mods: List[Dict] = field(
        default_factory=list
    )

    # ========================================================
    # CONFIG EDITOR
    # ========================================================

    marked_config_files: List[str] = field(
        default_factory=list
    )

    # ========================================================
    # MAINTENANCE / WIPE
    # ========================================================
    #
    # Files stored here are paths relative to server_root.
    #
    # Example:
    #
    #     mpmissions/dayzOffline.chernarusplus/db/file.xml
    #
    # Keeping these paths relative to server_root means the
    # wipe list does not depend on an absolute installation
    # path.

    marked_wipe_files: List[str] = field(
        default_factory=list
    )

    # ========================================================
    # FILES PANEL
    # ========================================================

    files_column_widths: List[int] = field(
        default_factory=lambda: [
            360,
            100,
            100,
            150,
            110,
        ]
    )

    # ========================================================
    # MODS PANEL
    # ========================================================

    mods_column_widths: List[int] = field(
        default_factory=lambda: [
            150,
            260,
            100,
            80,
            70,
            90,
            120,
        ]
    )

    # ========================================================
    # PATH HELPERS
    # ========================================================

    def update_server_paths(self):
        """
        Rebuild paths that belong to the DayZ server root.

        server_root is the single source of truth for:

            - keys
            - mpmissions
            - Workshop content

        profiles_dir is resolved from profiles_arg.

        A relative profiles_arg is resolved against
        server_root.

        An absolute profiles_arg is used directly.
        """

        root = (
            self.server_root.strip().rstrip("/")
        )

        if not root:
            return

        self.server_root = root

        # ----------------------------------------------------
        # SERVER-ROOT-OWNED PATHS
        # ----------------------------------------------------

        self.keys_dir = (
            f"{root}/keys"
        )

        self.mpmissions_dir = (
            f"{root}/mpmissions"
        )

        self.workshop_content_dir = (
            f"{root}/steamapps/"
            "workshop/content/221100"
        )

        # ----------------------------------------------------
        # PROFILES PATH
        # ----------------------------------------------------
        #
        # profiles_arg represents the actual DayZ
        # -profiles= parameter.
        #
        # Do not derive this from steamcmd_path or the
        # remote user's home directory.

        profiles_arg = (
            self.profiles_arg.strip()
        )

        if not profiles_arg:
            profiles_arg = "profiles"
            self.profiles_arg = profiles_arg

        if profiles_arg.startswith("/"):
            profiles_dir = profiles_arg.rstrip("/")

        else:
            profiles_dir = (
                f"{root}/"
                f"{profiles_arg.lstrip('/')}"
            )

        self.profiles_dir = profiles_dir
        self.log_dir = profiles_dir

    # ========================================================
    # CONFIGURATION STATUS
    # ========================================================

    def is_configured(self) -> bool:
        """
        Return True when the minimum SSH connection
        information has been configured.
        """

        return bool(
            self.host.strip()
            and self.username.strip()
            and self.key_path.strip()
        )

    # ========================================================
    # SAVE
    # ========================================================

    def save(self):
        """
        Save application configuration to disk.

        Server-root-derived paths and the resolved profiles
        directory are refreshed before saving so the stored
        configuration remains internally consistent.
        """

        self.update_server_paths()

        CONFIG_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            CONFIG_FILE,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                asdict(self),
                f,
                indent=2,
            )

    # ========================================================
    # LOAD
    # ========================================================

    @classmethod
    def load(cls) -> "AppConfig":
        """
        Load application configuration from disk.

        Unknown fields are ignored so newer versions remain
        compatible with older configuration files.

        Older configuration files may not contain profiles_arg.
        The default value "profiles" is then used.

        Older configuration files may not contain
        marked_wipe_files. The default empty list is then used.

        Server-root-derived paths are rebuilt after loading so
        stale paths from older configuration versions cannot
        override the current server_root.
        """

        if CONFIG_FILE.exists():
            try:
                with open(
                    CONFIG_FILE,
                    encoding="utf-8",
                ) as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    config = cls()
                    config.update_server_paths()
                    return config

                known_fields = (
                    cls.__dataclass_fields__
                )

                known = {
                    key: value
                    for key, value in data.items()
                    if key in known_fields
                }

                config = cls(**known)

                # Always rebuild paths that belong to the
                # configured DayZ server root.
                #
                # profiles_dir is also resolved here from
                # profiles_arg so the stored -profiles=
                # parameter remains authoritative.

                config.update_server_paths()

                return config

            except Exception:
                # If the configuration file is invalid or
                # cannot be read, fall back to defaults.
                pass

        config = cls()
        config.update_server_paths()

        return config
