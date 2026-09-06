import re
import shlex
import time

from pathlib import Path

from PySide6.QtCore import Qt, Signal

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QPlainTextEdit,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
)

from worker import WorkerRegistry


APPID_STABLE = "223350"
APPID_EXPERIMENTAL = "1042420"

# dzmanager.pbo is shipped alongside this application (not downloaded
# from Steam) and is copied to the remote server's addons directory
# over SFTP. This file lives in <project_root>/widgets/, and the pbo
# ships in <project_root>/pbo/, hence the parent.parent.
LOCAL_PBO_DIR = Path(__file__).resolve().parent.parent / "pbo"

DZMANAGER_PBO_FILENAME = "dzmanager.pbo"

# Directory name under the DayZ server install dir that dzmanager.pbo
# is deployed to/removed from. Already present on any deployed DayZ
# server, so this is never created here.
REMOTE_ADDON_SUBDIR = "addons"

STEAMCMD_DOWNLOAD_URL = (
    "https://steamcdn-a.akamaihd.net/client/installer/"
    "steamcmd_linux.tar.gz"
)

# SteamCMD frequently exits 0 even when a step actually failed
# (e.g. a failed app_update). The exit code alone can't be trusted.
STEAMCMD_ERROR_PATTERN = re.compile(
    r"ERROR!",
    re.IGNORECASE,
)

# If SteamCMD doesn't have cached credentials for this account on
# this server yet, +login falls back to interactive prompts for a
# password and then a Steam Guard code -- as two SEPARATE prompts.
# But SteamCMD's non-interactive login only accepts the Steam Guard
# code as a third positional argument on the *same* +login call
# ("+login user pass code"); it cannot be supplied to a follow-up
# prompt after the fact, and a Steam Guard code is only valid for a
# matter of seconds regardless. So instead of trying to answer these
# prompts one at a time, the moment any of them shows up we cancel
# that SteamCMD run outright and start a completely fresh one with
# all three values supplied together.
LOGIN_NEEDED_PATTERNS = (
    re.compile(
        r"cached credentials not found",
        re.IGNORECASE,
    ),
    re.compile(
        r"^password\s*:\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"steam\s*guard",
        re.IGNORECASE,
    ),
    re.compile(
        r"two[- ]factor",
        re.IGNORECASE,
    ),
    re.compile(
        r"enter the current code",
        re.IGNORECASE,
    ),
    re.compile(
        r"mobile authenticator",
        re.IGNORECASE,
    ),
)

# Maximum number of times to restart SteamCMD with freshly-entered
# credentials before giving up, as a safety net against a runaway
# retry loop (e.g. a mistyped password or an expired code keeps
# getting rejected).
MAX_LOGIN_ATTEMPTS = 3

# If SteamCMD goes silent for this long mid-command without exiting
# and without matching one of the patterns above, treat it as a
# probably-interactive stall too (SteamCMD normally prints frequent
# progress/keepalive output otherwise).
STEAMCMD_STALL_TIMEOUT_SECONDS = 30.0


class SteamCmdNeedsFreshLoginError(RuntimeError):
    """
    Raised when SteamCMD has no cached credentials for this account
    on this server and is falling back to an interactive prompt.

    This is deliberately not answered in place: SteamCMD only accepts
    a Steam Guard code as part of the original +login call, not as a
    reply to a later prompt, and the code expires within seconds
    anyway. The caller should cancel this run entirely and start a
    new one with freshly-collected credentials instead.
    """


class DeployPanel(QWidget):
    """
    Deploys SteamCMD and the DayZ dedicated server on the
    remote machine.

    Also checks whether installed DayZ branches are up-to-date
    by comparing the installed Steam build ID from the local
    appmanifest with the current Steam build ID reported by
    SteamCMD.

    Stable:
        AppID 223350
        Branch: public

    Experimental:
        AppID 1042420
        Branch: public

    The DayZ server install directory entered here is the
    authoritative server_root used throughout the application.

    Server-root-derived paths are maintained by AppConfig:

        <server_root>/keys
        <server_root>/mpmissions
        <server_root>/steamapps/workshop/content/221100

    The DayZ -profiles= parameter is intentionally NOT derived
    here. That value belongs to the Systemd service configuration.

    IMPORTANT SYSTEMD SAFETY:

    DeployPanel will NEVER overwrite an existing systemd unit.

    If the configured service already exists, deployment stops
    and the existing daemon is preserved.
    """

    # Emitted from the worker thread while SteamCMD is running so
    # output can be appended to the log widget safely on the GUI
    # thread (Qt marshals queued signal emissions across threads
    # automatically).
    steamcmd_output = Signal(str)

    # Emitted from the worker thread the moment SteamCMD indicates it
    # has no cached credentials for this account on this server.
    # Connected with Qt.BlockingQueuedConnection so emitting this
    # signal blocks the worker thread until the GUI-thread dialog
    # closes -- letting us collect (username, password, code) into
    # self._full_credential_result and read it back immediately after
    # emit() returns. Must only ever be emitted from a non-GUI thread;
    # emitting it from the GUI thread would deadlock.
    credential_dialog_requested = Signal(str, str)

    def __init__(
        self,
        ssh,
        config,
        sudo_password_getter=None,
    ):
        super().__init__()

        self.ssh = ssh
        self.config = config
        self.sudo_password_getter = sudo_password_getter

        self.jobs = WorkerRegistry()

        self.detected_steamcmd = None

        # Whether dzmanager.pbo is currently known to be present in
        # the server's addons directory. Kept up to date by
        # check_status() and by deploy/uninstall completing.
        self._pbo_deployed = False

        self._full_credential_result = None

        self._build_ui()

        self.steamcmd_output.connect(
            self._append
        )

        self.credential_dialog_requested.connect(
            self._show_full_credential_dialog,
            Qt.ConnectionType.BlockingQueuedConnection,
        )

        self.set_connected(False)

    # ========================================================
    # UI
    # ========================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()

        # ----------------------------------------------------
        # SteamCMD
        # ----------------------------------------------------

        self.steamcmd_path_edit = QLineEdit(
            self.config.steamcmd_path.strip()
            or "steamcmd"
        )

        self.steamcmd_path_edit.setPlaceholderText(
            "steamcmd for AUR/package install, "
            "or /path/to/steamcmd.sh for manual install"
        )

        # ----------------------------------------------------
        # Server paths
        # ----------------------------------------------------

        self.server_root_edit = QLineEdit(
            self.config.server_root
        )

        self.steam_user_edit = QLineEdit(
            self.config.steam_user
        )

        self.steam_password_edit = QLineEdit()

        self.steam_password_edit.setEchoMode(
            QLineEdit.Password
        )

        self.steam_password_edit.setPlaceholderText(
            "only for a non-anonymous account; "
            "kept in memory only"
        )

        form.addRow(
            "SteamCMD command / path",
            self.steamcmd_path_edit,
        )

        form.addRow(
            "DayZ server install dir",
            self.server_root_edit,
        )

        form.addRow(
            "Steam login",
            self.steam_user_edit,
        )

        form.addRow(
            "Steam password",
            self.steam_password_edit,
        )

        layout.addLayout(form)

        # ----------------------------------------------------
        # Information
        # ----------------------------------------------------

        note = QLabel(
            "SteamCMD can be installed manually or through your "
            "package manager/AUR. For an Arch package installation, "
            "use 'steamcmd' in the SteamCMD field. The app will "
            "resolve it through the remote user's PATH.\n\n"
            "DayZ dedicated server files can be downloaded anonymously. "
            "A Steam account is only required if you specifically need "
            "one for your setup.\n\n"
            "If this account has never logged in via SteamCMD on this "
            "server before, SteamCMD will need a password and/or a "
            "Steam Guard code. A Steam Guard code expires within "
            "seconds, so it can't be entered ahead of time here -- "
            "instead, a popup will appear during deployment at the "
            "exact moment SteamCMD asks for it. Once SteamCMD has "
            "cached the login, no further prompts are needed on "
            "future deployments."
        )

        note.setWordWrap(True)

        note.setStyleSheet(
            "color: gray;"
        )

        layout.addWidget(
            note
        )

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        check_row = QHBoxLayout()

        self.check_btn = QPushButton(
            "Check Installation / Updates"
        )

        self.check_btn.clicked.connect(
            self.check_status
        )

        check_row.addWidget(
            self.check_btn
        )

        check_row.addStretch()

        layout.addLayout(
            check_row
        )

        self.steamcmd_status = QLabel(
            "SteamCMD: (connect first)"
        )

        self.server_status = QLabel(
            "DayZ server: (connect first)"
        )

        # ----------------------------------------------------
        # Stable status + indicator
        # ----------------------------------------------------

        self.stable_indicator = QLabel(
            "●"
        )

        self.stable_status = QLabel(
            "Stable: (connect first)"
        )

        self.stable_indicator.setFixedWidth(
            18
        )

        # ----------------------------------------------------
        # Experimental status + indicator
        # ----------------------------------------------------

        self.experimental_indicator = QLabel(
            "●"
        )

        self.experimental_status = QLabel(
            "Experimental: (connect first)"
        )

        self.experimental_indicator.setFixedWidth(
            18
        )

        # ----------------------------------------------------
        # Stable row
        # ----------------------------------------------------

        stable_row = QHBoxLayout()

        stable_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        stable_row.setSpacing(
            4
        )

        stable_row.addWidget(
            self.stable_indicator
        )

        stable_row.addWidget(
            self.stable_status
        )

        stable_row.addStretch()

        # ----------------------------------------------------
        # Experimental row
        # ----------------------------------------------------

        experimental_row = QHBoxLayout()

        experimental_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        experimental_row.setSpacing(
            4
        )

        experimental_row.addWidget(
            self.experimental_indicator
        )

        experimental_row.addWidget(
            self.experimental_status
        )

        experimental_row.addStretch()

        # ----------------------------------------------------
        # Add status widgets
        # ----------------------------------------------------

        layout.addWidget(
            self.steamcmd_status
        )

        layout.addWidget(
            self.server_status
        )

        layout.addLayout(
            stable_row
        )

        layout.addLayout(
            experimental_row
        )

        # ----------------------------------------------------
        # Install buttons
        # ----------------------------------------------------

        install_row = QHBoxLayout()

        self.install_steamcmd_btn = QPushButton(
            "Install / Update SteamCMD"
        )

        self.install_steamcmd_btn.clicked.connect(
            self.install_steamcmd
        )

        self.install_stable_btn = QPushButton(
            "Install / Update Stable Server"
        )

        self.install_stable_btn.clicked.connect(
            lambda: self.install_server(
                APPID_STABLE,
                "stable",
            )
        )

        self.install_experimental_btn = QPushButton(
            "Install / Update Experimental Server"
        )

        self.install_experimental_btn.clicked.connect(
            lambda: self.install_server(
                APPID_EXPERIMENTAL,
                "experimental",
            )
        )

        install_row.addWidget(
            self.install_steamcmd_btn
        )

        install_row.addWidget(
            self.install_stable_btn
        )

        install_row.addWidget(
            self.install_experimental_btn
        )

        layout.addLayout(
            install_row
        )

        # ----------------------------------------------------
        # Systemd service
        # ----------------------------------------------------

        systemd_label = QLabel(
            "Systemd Daemon:"
        )

        systemd_label.setStyleSheet(
            "font-weight: bold;"
        )

        layout.addWidget(
            systemd_label
        )

        # ----------------------------------------------------
        # Systemd checklist indicators
        # ----------------------------------------------------

        self.systemd_file_indicator = QLabel(
            "●"
        )

        self.systemd_file_status = QLabel(
            "Unit file: (connect first)"
        )

        self.systemd_valid_indicator = QLabel(
            "●"
        )

        self.systemd_valid_status = QLabel(
            "Unit validity: (connect first)"
        )

        self.systemd_enabled_indicator = QLabel(
            "●"
        )

        self.systemd_enabled_status = QLabel(
            "Enabled: (connect first)"
        )

        self.systemd_running_indicator = QLabel(
            "●"
        )

        self.systemd_running_status = QLabel(
            "Running: (connect first)"
        )

        for indicator in (
            self.systemd_file_indicator,
            self.systemd_valid_indicator,
            self.systemd_enabled_indicator,
            self.systemd_running_indicator,
        ):
            indicator.setFixedWidth(
                18
            )

        # ----------------------------------------------------
        # Systemd checklist rows
        # ----------------------------------------------------

        systemd_file_row = self._make_status_row(
            self.systemd_file_indicator,
            self.systemd_file_status,
        )

        systemd_valid_row = self._make_status_row(
            self.systemd_valid_indicator,
            self.systemd_valid_status,
        )

        systemd_enabled_row = self._make_status_row(
            self.systemd_enabled_indicator,
            self.systemd_enabled_status,
        )

        systemd_running_row = self._make_status_row(
            self.systemd_running_indicator,
            self.systemd_running_status,
        )

        layout.addLayout(
            systemd_file_row
        )

        layout.addLayout(
            systemd_valid_row
        )

        layout.addLayout(
            systemd_enabled_row
        )

        layout.addLayout(
            systemd_running_row
        )

        # ----------------------------------------------------
        # Deploy Systemd Service
        # ----------------------------------------------------

        systemd_row = QHBoxLayout()

        self.deploy_systemd_btn = QPushButton(
            "Deploy Systemd Service"
        )

        self.deploy_systemd_btn.clicked.connect(
            self.deploy_systemd_service
        )

        systemd_row.addWidget(
            self.deploy_systemd_btn
        )

        # ----------------------------------------------------
        # Deploy dzmanager.pbo
        # ----------------------------------------------------

        self.deploy_pbo_btn = QPushButton(
            "Deploy dzmanager.pbo"
        )

        self.deploy_pbo_btn.setToolTip(
            "Deploys tools/pbo/dzmanager.pbo to the server's "
            "addons directory. Once deployed, this button "
            "switches to removing it instead."
        )

        self.deploy_pbo_btn.clicked.connect(
            self.deploy_dzmanager_pbo
        )

        systemd_row.addWidget(
            self.deploy_pbo_btn
        )

        systemd_row.addStretch()

        layout.addLayout(
            systemd_row
        )

        # ----------------------------------------------------
        # Apply settings
        # ----------------------------------------------------

        apply_row = QHBoxLayout()

        apply_row.addStretch()

        self.apply_btn = QPushButton(
            "Apply These Paths to Settings"
        )

        self.apply_btn.clicked.connect(
            self.apply_to_settings
        )

        apply_row.addWidget(
            self.apply_btn
        )

        layout.addLayout(
            apply_row
        )

        # ----------------------------------------------------
        # Log
        # ----------------------------------------------------

        layout.addWidget(
            QLabel("Log:")
        )

        self.log = QPlainTextEdit()

        self.log.setReadOnly(
            True
        )

        self.log.setStyleSheet(
            "font-family: monospace;"
        )

        layout.addWidget(
            self.log
        )

    def _make_status_row(
        self,
        indicator,
        status,
    ):
        row = QHBoxLayout()

        row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        row.setSpacing(
            4
        )

        row.addWidget(
            indicator
        )

        row.addWidget(
            status
        )

        row.addStretch()

        return row

    # ========================================================
    # CONNECTION STATE
    # ========================================================

    def set_connected(self, connected):
        connected = bool(
            connected
        )

        for widget in (
            self.check_btn,
            self.install_steamcmd_btn,
            self.install_stable_btn,
            self.install_experimental_btn,
            self.deploy_systemd_btn,
            self.deploy_pbo_btn,
            self.apply_btn,
        ):
            widget.setEnabled(
                connected
            )

        if connected:
            self.check_status()

        else:
            self.detected_steamcmd = None

            self.steamcmd_status.setText(
                "SteamCMD: (connect first)"
            )

            self.server_status.setText(
                "DayZ server: (connect first)"
            )

            self.stable_status.setText(
                "Stable: (connect first)"
            )

            self.experimental_status.setText(
                "Experimental: (connect first)"
            )

            self._set_indicator_neutral(
                self.stable_indicator
            )

            self._set_indicator_neutral(
                self.experimental_indicator
            )

            self.stable_status.setStyleSheet(
                ""
            )

            self.experimental_status.setStyleSheet(
                ""
            )

            self._reset_systemd_status()

            self._set_pbo_button_state(
                False
            )

    # ========================================================
    # CONFIG REFRESH
    # ========================================================

    def refresh_config_paths(self):
        self.steamcmd_path_edit.setText(
            self.config.steamcmd_path.strip()
            or "steamcmd"
        )

        self.server_root_edit.setText(
            self.config.server_root
        )

        self.steam_user_edit.setText(
            self.config.steam_user
        )

    # ========================================================
    # HELPERS
    # ========================================================

    def _append(self, text):
        if text:
            self.log.appendPlainText(
                str(text)
            )

    def _append_worker_output(
        self,
        text,
    ):
        # Safe to call from the worker thread: this only emits a
        # signal, which Qt queues onto the GUI thread. Never touch
        # self.log directly from a worker thread.
        self.steamcmd_output.emit(
            str(text)
        )

    def _set_busy(self, busy):
        busy = bool(
            busy
        )

        for widget in (
            self.check_btn,
            self.install_steamcmd_btn,
            self.install_stable_btn,
            self.install_experimental_btn,
            self.deploy_systemd_btn,
            self.deploy_pbo_btn,
            self.apply_btn,
        ):
            widget.setEnabled(
                not busy
            )

    def _require_connected(self):
        if not self.ssh.is_connected():
            QMessageBox.information(
                self,
                "Not connected",
                "Click Connect on the Server Status tab first.",
            )

            return False

        return True

    def _sudo_password(self):
        if self.sudo_password_getter:
            return (
                self.sudo_password_getter()
                or ""
            )

        return ""

    # ========================================================
    # SYSTEMD HELPERS
    # ========================================================

    def _systemd_service_name(self):
        service_name = (
            self.config.systemd_service.strip()
            or "dayz-server"
        )

        if service_name.endswith(
            ".service"
        ):
            service_name = service_name[:-8]

        return service_name

    def _systemd_service_path(self):
        return (
            "/etc/systemd/system/"
            f"{self._systemd_service_name()}.service"
        )

    def _systemd_profiles_value(self):
        return (
            getattr(
                self.config,
                "profiles_arg",
                "",
            ).strip()
            or "profiles"
        )

    def _build_systemd_unit(self):
        server_root = (
            self.server_root_edit.text().strip()
        )

        if not server_root:
            return None

        server_root = server_root.rstrip("/")

        service_user = (
            self.config.username.strip()
            or "dayz"
        )

        profiles_value = (
            self._systemd_profiles_value()
        )

        executable = (
            f"{server_root}/DayZServer"
        )

        working_directory = (
            f"{server_root}/"
        )

        return (
            "[Unit]\n"
            "Description=DayZ Dedicated Server\n"
            "Wants=network-online.target\n"
            "After=syslog.target network.target "
            "nss-lookup.target network-online.target\n"
            "\n"
            "[Service]\n"
            f"ExecStart={shlex.quote(executable)} "
            "-config=serverDZ.cfg "
            f"-profiles={shlex.quote(profiles_value)}\n"
            f"WorkingDirectory={shlex.quote(working_directory)}\n"
            "LimitNOFILE=100000\n"
            "ExecReload=/bin/kill -s HUP $MAINPID\n"
            "ExecStop=/bin/kill -s INT $MAINPID\n"
            f"User={shlex.quote(service_user)}\n"
            "Group=users\n"
            "RuntimeMaxSec=14520s\n"
            "Restart=always\n"
            "RestartSec=5s\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    def _check_systemd_service(self):
        path = (
            self._systemd_service_path()
        )

        service_name = (
            self._systemd_service_name()
        )

        exists_command = (
            "if test -f "
            + shlex.quote(path)
            + " || test -L "
            + shlex.quote(path)
            + "; then "
            "echo SYSTEMD_FILE_EXISTS; "
            "else "
            "echo SYSTEMD_FILE_MISSING; "
            "fi"
        )

        _code, output, error = self.ssh.exec(
            exists_command
        )

        exists = (
            "SYSTEMD_FILE_EXISTS" in output
        )

        result = {
            "exists": exists,
            "valid": None,
            "enabled": None,
            "active": None,
            "error": "",
            "output": "",
        }

        if error:
            result["error"] = error.strip()

        if not exists:
            return result

        verify_command = (
            "systemd-analyze verify "
            + shlex.quote(path)
        )

        verify_code, verify_stdout, verify_stderr = (
            self.ssh.exec(
                verify_command,
                timeout=60,
            )
        )

        result["valid"] = (
            verify_code == 0
        )

        if verify_stdout:
            result["output"] += (
                verify_stdout
            )

        if verify_stderr:
            result["error"] += (
                ("\n" if result["error"] else "")
                + verify_stderr
            )

        enabled_command = (
            "systemctl is-enabled "
            + shlex.quote(service_name)
            + " 2>/dev/null || true"
        )

        _enabled_code, enabled_stdout, _enabled_error = (
            self.ssh.exec(
                enabled_command
            )
        )

        enabled_state = (
            enabled_stdout.strip()
        )

        if enabled_state == "enabled":
            result["enabled"] = True

        elif enabled_state in (
            "disabled",
            "masked",
            "static",
            "indirect",
            "generated",
            "transient",
            "bad",
        ):
            result["enabled"] = False

        active_command = (
            "systemctl is-active "
            + shlex.quote(service_name)
            + " 2>/dev/null || true"
        )

        _active_code, active_stdout, _active_error = (
            self.ssh.exec(
                active_command
            )
        )

        active_state = (
            active_stdout.strip()
        )

        if active_state == "active":
            result["active"] = True

        elif active_state:
            result["active"] = False

        return result

    # ========================================================
    # SYSTEMD STATUS INDICATORS
    # ========================================================

    def _set_indicator_green(self, widget):
        widget.setStyleSheet(
            "color: #4CAF50;"
            "font-size: 16px;"
            "font-weight: bold;"
        )

    def _set_indicator_red(self, widget):
        widget.setStyleSheet(
            "color: #FF4D4D;"
            "font-size: 16px;"
            "font-weight: bold;"
        )

    def _set_indicator_orange(self, widget):
        widget.setStyleSheet(
            "color: #FF9800;"
            "font-size: 16px;"
            "font-weight: bold;"
        )

    def _set_indicator_neutral(self, widget):
        widget.setStyleSheet(
            "color: #808080;"
            "font-size: 16px;"
            "font-weight: bold;"
        )

    def _set_systemd_item(
        self,
        indicator,
        label,
        state,
        green_text,
        red_text,
        orange_text=None,
    ):
        if state is True:
            label.setText(
                green_text
            )

            label.setStyleSheet(
                "color: #4CAF50;"
                "font-weight: bold;"
            )

            self._set_indicator_green(
                indicator
            )

        elif state is False:
            label.setText(
                red_text
            )

            label.setStyleSheet(
                "color: #FF4D4D;"
                "font-weight: bold;"
            )

            self._set_indicator_red(
                indicator
            )

        else:
            label.setText(
                orange_text
                or red_text
            )

            label.setStyleSheet(
                "color: #FF9800;"
                "font-weight: bold;"
            )

            self._set_indicator_orange(
                indicator
            )

    def _reset_systemd_status(self):
        self._set_indicator_neutral(
            self.systemd_file_indicator
        )

        self._set_indicator_neutral(
            self.systemd_valid_indicator
        )

        self._set_indicator_neutral(
            self.systemd_enabled_indicator
        )

        self._set_indicator_neutral(
            self.systemd_running_indicator
        )

        self.systemd_file_status.setText(
            "Unit file: (connect first)"
        )

        self.systemd_valid_status.setText(
            "Unit validity: (connect first)"
        )

        self.systemd_enabled_status.setText(
            "Enabled: (connect first)"
        )

        self.systemd_running_status.setText(
            "Running: (connect first)"
        )

        self.systemd_file_status.setStyleSheet("")
        self.systemd_valid_status.setStyleSheet("")
        self.systemd_enabled_status.setStyleSheet("")
        self.systemd_running_status.setStyleSheet("")

    def _display_systemd_status(self, result):
        exists = result.get("exists")
        valid = result.get("valid")
        enabled = result.get("enabled")
        active = result.get("active")

        self._set_systemd_item(
            self.systemd_file_indicator,
            self.systemd_file_status,
            exists,
            "Unit file: EXISTS",
            "Unit file: NOT installed",
            "Unit file: status unknown",
        )

        if not exists:
            self._set_systemd_item(
                self.systemd_valid_indicator,
                self.systemd_valid_status,
                None,
                "Unit validity: VALID",
                "Unit validity: NOT available",
                "Unit validity: not checked",
            )
        else:
            self._set_systemd_item(
                self.systemd_valid_indicator,
                self.systemd_valid_status,
                valid,
                "Unit validity: VALID",
                "Unit validity: INVALID",
                "Unit validity: unknown",
            )

        if not exists:
            self._set_systemd_item(
                self.systemd_enabled_indicator,
                self.systemd_enabled_status,
                None,
                "Enabled: YES",
                "Enabled: NO",
                "Enabled: not available",
            )
        else:
            self._set_systemd_item(
                self.systemd_enabled_indicator,
                self.systemd_enabled_status,
                enabled,
                "Enabled: YES",
                "Enabled: NO",
                "Enabled: unknown",
            )

        if not exists:
            self._set_systemd_item(
                self.systemd_running_indicator,
                self.systemd_running_status,
                None,
                "Running: YES",
                "Running: NO",
                "Running: not installed",
            )
        else:
            self._set_systemd_item(
                self.systemd_running_indicator,
                self.systemd_running_status,
                active,
                "Running: YES",
                "Running: NO",
                "Running: unknown",
            )

    def _set_branch_status(self, label, result):
        if label.lower() == "stable":
            text_widget = self.stable_status
            indicator_widget = self.stable_indicator
        else:
            text_widget = self.experimental_status
            indicator_widget = self.experimental_indicator

        text_widget.setText(
            self._format_branch_status(
                label,
                result,
            )
        )

        installed = result.get("installed")
        needs_update = result.get("needs_update")

        if installed and needs_update is False:
            self._set_indicator_green(
                indicator_widget
            )

            text_widget.setStyleSheet(
                "color: #4CAF50;"
                "font-weight: bold;"
            )

        elif installed and needs_update is True:
            self._set_indicator_red(
                indicator_widget
            )

            text_widget.setStyleSheet(
                "color: #FF4D4D;"
                "font-weight: bold;"
            )

        elif installed:
            self._set_indicator_orange(
                indicator_widget
            )

            text_widget.setStyleSheet(
                "color: #FF9800;"
                "font-weight: bold;"
            )

        else:
            self._set_indicator_neutral(
                indicator_widget
            )

            text_widget.setStyleSheet("")

    # ========================================================
    # STEAMCMD DETECTION
    # ========================================================

    def _steamcmd_detection_command(self, configured):
        configured = configured.strip()

        if not configured:
            configured = "steamcmd"

        if "/" in configured:
            quoted = shlex.quote(
                configured
            )

            return (
                f"if test -x {quoted}; then "
                f"echo {quoted}; "
                f"elif test -f {quoted}; then "
                f"echo {quoted}; "
                f"fi"
            )

        quoted = shlex.quote(
            configured
        )

        return (
            f"command -v {quoted} 2>/dev/null || true"
        )

    def _detect_steamcmd(self, configured):
        command = self._steamcmd_detection_command(
            configured
        )

        _code, stdout, _stderr = self.ssh.exec(
            command
        )

        detected = stdout.strip()

        if not detected:
            return None

        for line in detected.splitlines():
            line = line.strip()

            if line:
                return line

        return None

    # ========================================================
    # STEAM MANIFEST HELPERS
    # ========================================================

    def _manifest_path(
        self,
        server_root,
        appid,
    ):
        return (
            f"{server_root.rstrip('/')}/steamapps/"
            f"appmanifest_{appid}.acf"
        )

    def _binary_path(
        self,
        server_root,
    ):
        return (
            f"{server_root.rstrip('/')}/DayZServer"
        )

    def _read_installed_build_id(
        self,
        server_root,
        appid,
    ):
        manifest = self._manifest_path(
            server_root,
            appid,
        )

        command = (
            f"if test -f {shlex.quote(manifest)}; then "
            f"grep -E '\"buildid\"' "
            f"{shlex.quote(manifest)} | "
            f"head -n 1; "
            f"fi"
        )

        _code, stdout, _stderr = self.ssh.exec(
            command
        )

        match = re.search(
            r'"buildid"\s+"?([0-9]+)"?',
            stdout,
        )

        if not match:
            return None

        return match.group(1)

    # ========================================================
    # STEAM CURRENT BUILD
    # ========================================================

    def _steam_current_build_id(
        self,
        steamcmd,
        appid,
    ):
        command = (
            f"{shlex.quote(steamcmd)} "
            f"+app_info_update 1 "
            f"+app_info_print {shlex.quote(str(appid))} "
            f"+quit"
        )

        code, stdout, stderr = self.ssh.exec(
            command,
            timeout=180,
        )

        combined = (
            (stdout or "")
            + "\n"
            + (stderr or "")
        )

        if code != 0 and not combined.strip():
            return None

        public_match = re.search(
            r'"public"\s*'
            r'\{'
            r'(.*?)'
            r'\}',
            combined,
            re.DOTALL,
        )

        if public_match:
            public_section = (
                public_match.group(1)
            )

            build_match = re.search(
                r'"buildid"\s+"?([0-9]+)"?',
                public_section,
            )

            if build_match:
                return build_match.group(1)

        public_build_match = re.search(
            r'"public"\s*\{[^{}]*?'
            r'"buildid"\s+"?([0-9]+)"?',
            combined,
            re.DOTALL,
        )

        if public_build_match:
            return public_build_match.group(1)

        return None

    # ========================================================
    # BRANCH CHECK
    # ========================================================

    def _check_branch(
        self,
        steamcmd,
        server_root,
        appid,
    ):
        manifest = self._manifest_path(
            server_root,
            appid,
        )

        binary = self._binary_path(
            server_root
        )

        check_command = (
            f"if test -f {shlex.quote(manifest)}; "
            f"then echo MANIFEST; fi; "
            f"if test -f {shlex.quote(binary)}; "
            f"then echo BINARY; fi"
        )

        _code, output, _error = self.ssh.exec(
            check_command
        )

        installed = (
            "MANIFEST" in output
        )

        has_binary = (
            "BINARY" in output
        )

        result = {
            "installed": installed,
            "binary": has_binary,
            "installed_build": None,
            "current_build": None,
            "needs_update": None,
        }

        if not installed:
            return result

        result["installed_build"] = (
            self._read_installed_build_id(
                server_root,
                appid,
            )
        )

        if not steamcmd:
            return result

        result["current_build"] = (
            self._steam_current_build_id(
                steamcmd,
                appid,
            )
        )

        installed_build = (
            result["installed_build"]
        )

        current_build = (
            result["current_build"]
        )

        if (
            installed_build
            and current_build
        ):
            result["needs_update"] = (
                installed_build
                != current_build
            )

        return result

    # ========================================================
    # CHECK STATUS
    # ========================================================

    def check_status(self):
        if not self._require_connected():
            return

        steamcmd_config = (
            self.steamcmd_path_edit.text().strip()
            or "steamcmd"
        )

        server_root = (
            self.server_root_edit.text().strip()
        )

        if not server_root:
            QMessageBox.warning(
                self,
                "Missing server directory",
                "Enter the DayZ server installation directory.",
            )

            return

        self._set_busy(True)

        self.steamcmd_status.setText(
            "SteamCMD: checking..."
        )

        self.server_status.setText(
            "DayZ server: checking..."
        )

        self.stable_status.setText(
            "Stable: checking..."
        )

        self.experimental_status.setText(
            "Experimental: checking..."
        )

        self._set_indicator_neutral(
            self.stable_indicator
        )

        self._set_indicator_neutral(
            self.experimental_indicator
        )

        self.stable_status.setStyleSheet("")
        self.experimental_status.setStyleSheet("")

        self.systemd_file_status.setText(
            "Unit file: checking..."
        )

        self.systemd_valid_status.setText(
            "Unit validity: checking..."
        )

        self.systemd_enabled_status.setText(
            "Enabled: checking..."
        )

        self.systemd_running_status.setText(
            "Running: checking..."
        )

        self._set_indicator_neutral(
            self.systemd_file_indicator
        )

        self._set_indicator_neutral(
            self.systemd_valid_indicator
        )

        self._set_indicator_neutral(
            self.systemd_enabled_indicator
        )

        self._set_indicator_neutral(
            self.systemd_running_indicator
        )

        def task():
            detected = self._detect_steamcmd(
                steamcmd_config
            )

            stable = self._check_branch(
                detected,
                server_root,
                APPID_STABLE,
            )

            experimental = self._check_branch(
                detected,
                server_root,
                APPID_EXPERIMENTAL,
            )

            systemd = self._check_systemd_service()

            _, pbo_remote_path = (
                self._remote_addon_paths(
                    server_root
                )
            )

            try:
                pbo_deployed = (
                    self._check_pbo_deployed(
                        pbo_remote_path
                    )
                )

                pbo_check_error = None

            except Exception as exc:
                # Could not confirm either way (dropped connection,
                # permissions, missing addons dir, etc.) -- this is
                # NOT the same as confirming the file is gone, so we
                # deliberately do not default pbo_deployed to False
                # here. The caller keeps whatever state it last knew.
                pbo_deployed = None

                pbo_check_error = str(exc)

            return {
                "steamcmd": detected,
                "stable": stable,
                "experimental": experimental,
                "systemd": systemd,
                "pbo_deployed": pbo_deployed,
                "pbo_check_error": pbo_check_error,
            }

        def fail(error):
            self._set_busy(False)

            self._append(
                f"Status check failed: {error}"
            )

            QMessageBox.warning(
                self,
                "Error",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=self._on_status_checked,
            on_fail=fail,
        )

    # ========================================================
    # STATUS DISPLAY
    # ========================================================

    def _format_branch_status(
        self,
        label,
        result,
    ):
        if not result["installed"]:
            return (
                f"{label}: NOT installed"
            )

        installed_build = (
            result["installed_build"]
        )

        current_build = (
            result["current_build"]
        )

        needs_update = (
            result["needs_update"]
        )

        binary_note = ""

        if not result["binary"]:
            binary_note = (
                " — binary missing"
            )

        if (
            installed_build
            and current_build
        ):
            if needs_update:
                return (
                    f"{label}: NEED UPDATE"
                    f"{binary_note}"
                    f" — installed build "
                    f"{installed_build}, "
                    f"current build "
                    f"{current_build}"
                )

            return (
                f"{label}: UP-TO-DATE"
                f"{binary_note}"
                f" — build "
                f"{installed_build}"
            )

        if installed_build:
            return (
                f"{label}: installed"
                f"{binary_note}"
                " — update status unknown"
                f" — installed build "
                f"{installed_build}"
            )

        return (
            f"{label}: installed"
            f"{binary_note}"
            " — update status unknown"
        )

    def _on_status_checked(
        self,
        result,
    ):
        detected = result["steamcmd"]
        stable = result["stable"]
        experimental = result["experimental"]
        systemd = result["systemd"]

        pbo_deployed = result.get(
            "pbo_deployed"
        )

        if pbo_deployed is None:
            self._append(
                "dzmanager.pbo status could not be "
                "confirmed, leaving button as-is: "
                + (
                    result.get("pbo_check_error")
                    or "unknown error"
                )
            )

        else:
            self._set_pbo_button_state(
                pbo_deployed
            )

        if detected:
            self.steamcmd_status.setText(
                f"SteamCMD: found ({detected})"
            )
        else:
            configured = (
                self.steamcmd_path_edit.text().strip()
                or "steamcmd"
            )

            self.steamcmd_status.setText(
                f"SteamCMD: NOT found ({configured})"
            )

        stable_installed = (
            stable["installed"]
        )

        experimental_installed = (
            experimental["installed"]
        )

        branches = []

        if stable_installed:
            branches.append("stable")

        if experimental_installed:
            branches.append("experimental")

        if branches:
            binary_missing = (
                (
                    stable_installed
                    and not stable["binary"]
                )
                or (
                    experimental_installed
                    and not experimental["binary"]
                )
            )

            if binary_missing:
                self.server_status.setText(
                    "DayZ server: installed "
                    f"({', '.join(branches)})"
                    " — binary missing"
                )
            else:
                self.server_status.setText(
                    "DayZ server: installed "
                    f"({', '.join(branches)})"
                )
        else:
            self.server_status.setText(
                "DayZ server: NOT installed"
            )

        self._set_branch_status(
            "Stable",
            stable,
        )

        self._set_branch_status(
            "Experimental",
            experimental,
        )

        self._display_systemd_status(
            systemd
        )

        service_name = (
            self._systemd_service_name()
        )

        if not systemd["exists"]:
            self._append(
                f"Systemd service {service_name}.service: "
                "NOT installed"
            )
        else:
            self._append(
                f"Systemd service {service_name}.service: "
                "EXISTS — existing daemon preserved"
            )

            if systemd["valid"] is True:
                self._append(
                    "Systemd service: VALID"
                )
            elif systemd["valid"] is False:
                self._append(
                    "Systemd service: INVALID"
                )
            else:
                self._append(
                    "Systemd service: validity unknown"
                )

            if systemd["enabled"] is True:
                self._append(
                    "Systemd service: ENABLED"
                )
            elif systemd["enabled"] is False:
                self._append(
                    "Systemd service: NOT enabled"
                )

            if systemd["active"] is True:
                self._append(
                    "Systemd service: RUNNING"
                )
            elif systemd["active"] is False:
                self._append(
                    "Systemd service: NOT running"
                )

        if systemd["output"]:
            self._append(
                systemd["output"].strip()
            )

        if systemd["error"]:
            self._append(
                systemd["error"].strip()
            )

        self._append(
            "--- DayZ update status check ---"
        )

        if stable["installed"]:
            self._append(
                "Stable installed build: "
                f"{stable['installed_build'] or 'unknown'}"
            )

            self._append(
                "Stable current build: "
                f"{stable['current_build'] or 'unknown'}"
            )

        if experimental["installed"]:
            self._append(
                "Experimental installed build: "
                f"{experimental['installed_build'] or 'unknown'}"
            )

            self._append(
                "Experimental current build: "
                f"{experimental['current_build'] or 'unknown'}"
            )

        self._append("")

        self._set_busy(False)

    # ========================================================
    # INSTALL / UPDATE STEAMCMD
    # ========================================================

    def install_steamcmd(self):
        if not self._require_connected():
            return

        configured = (
            self.steamcmd_path_edit.text().strip()
            or "steamcmd"
        )

        self._append(
            "--- Checking for existing SteamCMD ---"
        )

        self._set_busy(True)

        def detect_task():
            return self._detect_steamcmd(
                configured
            )

        def fail(error):
            self._append(
                f"ERROR: {error}"
            )

            self._set_busy(False)

            QMessageBox.warning(
                self,
                "Error",
                str(error),
            )

        self.jobs.start(
            detect_task,
            on_ok=self._on_steamcmd_detected_for_install,
            on_fail=fail,
        )

    def _on_steamcmd_detected_for_install(
        self,
        detected,
    ):
        if detected:
            self.detected_steamcmd = detected

            self._append(
                f"SteamCMD already installed: {detected}"
            )

            self.steamcmd_status.setText(
                f"SteamCMD: found ({detected})"
            )

            QMessageBox.information(
                self,
                "SteamCMD Already Installed",
                (
                    "SteamCMD is already available on the "
                    "remote server.\n\n"
                    f"Detected executable:\n{detected}\n\n"
                    "For a package-installed version such as "
                    "Arch/AUR SteamCMD, update it through your "
                    "package manager."
                ),
            )

            self._set_busy(False)

            return

        configured = (
            self.steamcmd_path_edit.text().strip()
            or "steamcmd"
        )

        if "/" not in configured:
            QMessageBox.information(
                self,
                "SteamCMD Not Found",
                (
                    f"'{configured}' was not found on the "
                    "remote PATH.\n\n"
                    "If you want the app to manage a downloaded "
                    "SteamCMD installation, enter a full path such as:\n\n"
                    "/home/dayz/steamcmd/steamcmd.sh\n\n"
                    "Then run Install / Update SteamCMD again."
                ),
            )

            self._set_busy(False)

            return

        steamcmd_path = configured

        steamcmd_dir = (
            steamcmd_path.rsplit(
                "/",
                1,
            )[0]
        )

        def task():
            cmd = (
                f"mkdir -p {shlex.quote(steamcmd_dir)} && "
                f"cd {shlex.quote(steamcmd_dir)} && "
                f"curl -fsSL "
                f"{shlex.quote(STEAMCMD_DOWNLOAD_URL)} | "
                f"tar zxf -"
            )

            return self.ssh.exec(
                cmd,
                timeout=300,
            )

        self._append(
            "--- Installing SteamCMD ---"
        )

        self.jobs.start(
            task,
            on_ok=lambda result: (
                self._on_generic_done(
                    "SteamCMD install",
                    result,
                )
            ),
            on_fail=lambda error: (
                self._append(
                    f"ERROR: {error}"
                ),
                self._set_busy(False),
            ),
        )

    # ========================================================
    # INSTALL / UPDATE DAYZ SERVER
    # ========================================================

    def install_server(
        self,
        appid,
        label,
    ):
        if not self._require_connected():
            return

        steamcmd_config = (
            self.steamcmd_path_edit.text().strip()
            or "steamcmd"
        )

        server_root = (
            self.server_root_edit.text().strip()
        )

        steam_user = (
            self.steam_user_edit.text().strip()
            or "anonymous"
        )

        steam_password = (
            self.steam_password_edit.text().strip()
        )

        if not server_root:
            QMessageBox.warning(
                self,
                "Missing server directory",
                "Enter the DayZ server installation directory.",
            )

            return

        self._append(
            ""
        )

        self._append(
            f"--- Preparing {label} DayZ server installation ---"
        )

        self._append(
            f"App ID: {appid}"
        )

        self._append(
            f"Server root: {server_root}"
        )

        self._append(
            "--- Detecting SteamCMD ---"
        )

        self._set_busy(True)

        def detect_task():
            return self._detect_steamcmd(
                steamcmd_config
            )

        def fail(error):
            self._append(
                f"ERROR: {error}"
            )

            self._set_busy(False)

            QMessageBox.warning(
                self,
                "SteamCMD Detection Failed",
                str(error),
            )

        self.jobs.start(
            detect_task,
            on_ok=lambda detected: (
                self._install_server_with_detected_steamcmd(
                    detected,
                    appid,
                    label,
                    server_root,
                    steam_user,
                    steam_password,
                )
            ),
            on_fail=fail,
        )

    @staticmethod
    def _clean_steamcmd_text(text):
        """
        Strip common terminal control sequences from SteamCMD's PTY
        output while keeping progress text readable.
        """

        if not text:
            return ""

        text = str(text)

        text = re.sub(
            r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])",
            "",
            text,
        )

        text = text.replace("\x00", "")
        text = text.replace("\x08", "")

        return text

    @staticmethod
    def _looks_like_login_needed(text):
        if not text:
            return False

        return any(
            pattern.search(text)
            for pattern in LOGIN_NEEDED_PATTERNS
        )

    def _show_full_credential_dialog(
        self,
        initial_username,
        message,
    ):
        """
        Runs on the GUI thread via a BlockingQueuedConnection, so the
        worker thread is paused for the entire duration this dialog
        is open. Stores the result on self so the worker thread can
        read it back the instant emit() returns.

        Collects username, password, and Steam Guard code together
        in one shot, since SteamCMD only accepts the code as part of
        the original +login call and it expires within seconds --
        there is no point asking for it separately or in advance.
        """

        dialog = QDialog(self)

        dialog.setWindowTitle(
            "Steam Login Required"
        )

        layout = QVBoxLayout(dialog)

        info_label = QLabel(message)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        form = QFormLayout()

        username_edit = QLineEdit(
            initial_username or ""
        )

        password_edit = QLineEdit()
        password_edit.setEchoMode(
            QLineEdit.Password
        )

        code_edit = QLineEdit()
        code_edit.setPlaceholderText(
            "enter the current code now -- it expires in seconds"
        )

        form.addRow(
            "Steam username",
            username_edit,
        )

        form.addRow(
            "Steam password",
            password_edit,
        )

        form.addRow(
            "Steam Guard code",
            code_edit,
        )

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
            | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(
            dialog.accept
        )

        buttons.rejected.connect(
            dialog.reject
        )

        layout.addWidget(buttons)

        if initial_username:
            password_edit.setFocus()
        else:
            username_edit.setFocus()

        result = dialog.exec()

        if result != QDialog.Accepted:
            self._full_credential_result = None
            return

        username = username_edit.text().strip()
        password = password_edit.text().strip()
        code = code_edit.text().strip()

        if not username or not password or not code:
            QMessageBox.warning(
                self,
                "Missing Information",
                "Steam username, password, and the current Steam "
                "Guard code are all required to log in.",
            )

            self._full_credential_result = None
            return

        self._full_credential_result = (
            username,
            password,
            code,
        )

    def _request_full_credentials(
        self,
        initial_username,
        message,
    ):
        """
        Must only be called from a worker thread. Blocks that thread
        until the user submits (or cancels) the dialog on the GUI
        thread, then returns an (username, password, code) tuple, or
        None if cancelled/incomplete.
        """

        self._full_credential_result = None

        self.credential_dialog_requested.emit(
            initial_username or "",
            message,
        )

        return self._full_credential_result

    def _emit_deploy_chunk(
        self,
        buffer,
        data,
        stream_name,
        error_lines,
    ):
        """
        Process one chunk of live SteamCMD output: stream completed
        lines to the log and collect any ERROR! lines.

        Returns the unconsumed remainder (a line still being
        received, with no trailing newline yet).
        """

        if data:
            buffer += data

        buffer = buffer.replace("\r\n", "\n")
        buffer = buffer.replace("\r", "\n")

        parts = buffer.split("\n")
        complete = parts[:-1]
        remainder = parts[-1]

        for line in complete:
            line = self._clean_steamcmd_text(
                line
            ).strip()

            if not line:
                continue

            if STEAMCMD_ERROR_PATTERN.search(
                line
            ):
                error_lines.append(
                    line
                )

            if stream_name == "stderr":
                self._append_worker_output(
                    f"[SteamCMD STDERR] {line}"
                )
            else:
                self._append_worker_output(
                    line
                )

        return remainder

    def _run_steamcmd_deploy_live(
        self,
        command,
    ):
        """
        Run a SteamCMD command over a live, PTY-backed SSH channel,
        streaming output as it arrives.

        The moment SteamCMD indicates it has no cached credentials
        for this account on this server (an explicit "Cached
        credentials not found." message, or a bare password/Steam
        Guard prompt), this run is cancelled immediately by closing
        the channel -- which kills the remote SteamCMD process -- and
        SteamCmdNeedsFreshLoginError is raised. SteamCMD only accepts
        a Steam Guard code as part of the original +login call, not
        as a reply to a later prompt, and the code expires within
        seconds anyway, so there is no point trying to answer these
        prompts in place. The caller is expected to collect fresh
        credentials and start an entirely new run.

        If SteamCMD goes silent for an extended period without
        exiting and without matching a recognized pattern, that is
        treated the same way, in case of a prompt-text variant we
        don't otherwise recognize.

        Also treats an explicit "ERROR!" line as a hard failure even
        if SteamCMD's own exit code comes back 0, since SteamCMD is
        known to do that for a failed app_update/login too.

        Returns the process exit code on success.
        """

        self._append_worker_output(
            "$ " + command
        )

        if self.ssh.client is None:
            raise RuntimeError(
                "SSH connection is not available."
            )

        transport = (
            self.ssh.client.get_transport()
        )

        if (
            transport is None
            or not transport.is_active()
        ):
            raise RuntimeError(
                "SSH connection is not active."
            )

        channel = transport.open_session()

        try:
            try:
                channel.get_pty(
                    term="xterm",
                    width=160,
                    height=40,
                )
            except Exception:
                # Some SSH servers may reject PTY allocation; plain
                # channel output still works without one.
                pass

            channel.exec_command(
                command
            )

            stdout_buffer = ""
            stderr_buffer = ""
            error_lines = []

            last_output_time = time.monotonic()

            while True:
                had_data = False

                if channel.recv_ready():
                    data = channel.recv(
                        8192
                    )

                    if data:
                        had_data = True

                        text = data.decode(
                            "utf-8",
                            errors="replace",
                        )

                        stdout_buffer = (
                            self._emit_deploy_chunk(
                                stdout_buffer,
                                text,
                                "stdout",
                                error_lines,
                            )
                        )

                        last_output_time = (
                            time.monotonic()
                        )

                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(
                        8192
                    )

                    if data:
                        had_data = True

                        text = data.decode(
                            "utf-8",
                            errors="replace",
                        )

                        stderr_buffer = (
                            self._emit_deploy_chunk(
                                stderr_buffer,
                                text,
                                "stderr",
                                error_lines,
                            )
                        )

                        last_output_time = (
                            time.monotonic()
                        )

                # A login prompt normally has no trailing newline
                # (e.g. "password: " sits waiting right after the
                # colon), so check both remainders every pass, not
                # just completed lines.
                for remainder in (
                    stdout_buffer,
                    stderr_buffer,
                ):
                    cleaned_remainder = (
                        self._clean_steamcmd_text(
                            remainder
                        ).strip()
                    )

                    if self._looks_like_login_needed(
                        cleaned_remainder
                    ):
                        self._append_worker_output(
                            cleaned_remainder
                        )

                        raise SteamCmdNeedsFreshLoginError(
                            cleaned_remainder
                        )

                if channel.exit_status_ready():
                    if not (
                        channel.recv_ready()
                        or channel.recv_stderr_ready()
                    ):
                        break

                if not had_data:
                    stalled_for = (
                        time.monotonic()
                        - last_output_time
                    )

                    if (
                        not channel.exit_status_ready()
                        and stalled_for
                        > STEAMCMD_STALL_TIMEOUT_SECONDS
                    ):
                        raise SteamCmdNeedsFreshLoginError(
                            "SteamCMD has produced no output for "
                            f"{int(STEAMCMD_STALL_TIMEOUT_SECONDS)} "
                            "seconds without exiting, which almost "
                            "always means it's waiting on "
                            "interactive input."
                        )

                    time.sleep(0.05)

            if stdout_buffer:
                stdout_buffer = self._clean_steamcmd_text(
                    stdout_buffer
                ).strip()

                if stdout_buffer:
                    if STEAMCMD_ERROR_PATTERN.search(
                        stdout_buffer
                    ):
                        error_lines.append(
                            stdout_buffer
                        )

                    self._append_worker_output(
                        stdout_buffer
                    )

            if stderr_buffer:
                stderr_buffer = self._clean_steamcmd_text(
                    stderr_buffer
                ).strip()

                if stderr_buffer:
                    if STEAMCMD_ERROR_PATTERN.search(
                        stderr_buffer
                    ):
                        error_lines.append(
                            stderr_buffer
                        )

                    self._append_worker_output(
                        f"[SteamCMD STDERR] {stderr_buffer}"
                    )

            exit_code = (
                channel.recv_exit_status()
            )

            if exit_code == 0:
                self._append_worker_output(
                    "SteamCMD finished successfully."
                )
            else:
                self._append_worker_output(
                    f"SteamCMD FAILED with exit code {exit_code}."
                )

            if error_lines:
                self._append_worker_output(
                    "SteamCMD reported at least one error line "
                    "despite the process exit code:"
                )

                for line in error_lines:
                    self._append_worker_output(
                        f"  {line}"
                    )

                raise RuntimeError(
                    "SteamCMD reported a failure even though it "
                    f"exited with code {exit_code}:\n"
                    + "\n".join(error_lines)
                )

            return exit_code

        finally:
            # Closing the channel while SteamCMD is still running
            # (e.g. right after detecting a login-needed condition)
            # terminates the remote SteamCMD process along with it.
            try:
                channel.close()
            except Exception:
                pass

    def _install_server_with_detected_steamcmd(
        self,
        detected,
        appid,
        label,
        server_root,
        steam_user,
        steam_password,
    ):
        if not detected:
            configured = (
                self.steamcmd_path_edit.text().strip()
                or "steamcmd"
            )

            self._set_busy(False)

            self._append(
                f"SteamCMD not found: {configured}"
            )

            QMessageBox.warning(
                self,
                "SteamCMD Not Found",
                (
                    f"Could not find '{configured}' on the "
                    "remote server.\n\n"
                    "Install SteamCMD through your package manager "
                    "or enter the full path to steamcmd.sh."
                ),
            )

            return

        self.detected_steamcmd = detected

        self.steamcmd_status.setText(
            f"SteamCMD: found ({detected})"
        )

        self._append(
            f"Using SteamCMD: {detected}"
        )

        # ----------------------------------------------------
        # Build SteamCMD command
        #
        # Keep the DayZ download completely independent from
        # the systemd deployment code.
        # ----------------------------------------------------

        server_root = server_root.rstrip("/")

        steamcmd = shlex.quote(
            detected
        )

        quoted_root = shlex.quote(
            server_root
        )

        def build_cmd(
            current_user,
            current_password,
            current_code,
        ):
            login_part = (
                f"+login {shlex.quote(current_user)}"
            )

            if current_password:
                login_part += (
                    f" {shlex.quote(current_password)}"
                )

                if current_code:
                    login_part += (
                        f" {shlex.quote(current_code)}"
                    )

            return (
                f"mkdir -p {quoted_root} && "
                f"cd {quoted_root} && "
                f"{steamcmd} "
                f"+force_install_dir {quoted_root} "
                f"{login_part} "
                f"+app_update {shlex.quote(str(appid))} validate "
                f"+quit"
            )

        # IMPORTANT:
        # This GUI update must happen before the worker starts.
        # The function passed to WorkerRegistry.start() runs on
        # the Worker thread and must never touch Qt widgets.
        self._append(
            f"Creating server directory: {server_root}"
        )

        def task():
            current_user = steam_user
            current_password = steam_password
            current_code = None

            attempt = 0
            code = None
            error = ""

            while True:
                cmd = build_cmd(
                    current_user,
                    current_password,
                    current_code,
                )

                try:
                    code = self._run_steamcmd_deploy_live(
                        cmd
                    )

                    error = ""

                    break

                except SteamCmdNeedsFreshLoginError as exc:
                    attempt += 1

                    self._append_worker_output(
                        "SteamCMD has no cached login for this "
                        "account on this server -- cancelling this "
                        "run and asking for fresh credentials."
                    )

                    if attempt > MAX_LOGIN_ATTEMPTS:
                        code = 1

                        error = (
                            "SteamCMD still could not log in after "
                            f"{MAX_LOGIN_ATTEMPTS} attempt(s) with "
                            "freshly-entered credentials. "
                            "Double-check the account name, "
                            "password, and Steam Guard code.\n\n"
                            f"Last message from SteamCMD: {exc}"
                        )

                        break

                    creds = (
                        self._request_full_credentials(
                            current_user,
                            (
                                "SteamCMD has no cached credentials "
                                "for this account on this server, "
                                "so it needs your password and a "
                                "current Steam Guard code.\n\n"
                                "All three are sent to SteamCMD "
                                "together the moment you click OK, "
                                "since the code is only valid for a "
                                "few seconds -- have your "
                                "authenticator ready before "
                                "confirming."
                            ),
                        )
                    )

                    if creds is None:
                        code = 1

                        error = (
                            "Deployment cancelled: SteamCMD needed "
                            "fresh credentials and none were "
                            "provided."
                        )

                        break

                    (
                        current_user,
                        current_password,
                        current_code,
                    ) = creds

                except RuntimeError as exc:
                    # Any other detected failure (an explicit
                    # ERROR! line, a lost connection, etc). Surface
                    # it as a normal SteamCMD failure rather than
                    # letting the whole job fail with a traceback --
                    # the message is already user-actionable.
                    code = 1
                    error = str(exc)
                    break

            # Output has already been streamed live via
            # _append_worker_output, so there's nothing further to
            # hand back here.
            output = ""

            # ------------------------------------------------
            # Verify that SteamCMD actually created the
            # expected DayZ installation.
            #
            # This is deliberately done BEFORE returning
            # success to the GUI.
            # ------------------------------------------------

            manifest = self._manifest_path(
                server_root,
                appid,
            )

            binary = self._binary_path(
                server_root
            )

            verify_command = (
                "echo '--- Installation verification ---'; "
                f"if test -f {shlex.quote(manifest)}; then "
                "echo DAYZ_MANIFEST_OK; "
                "else "
                "echo DAYZ_MANIFEST_MISSING; "
                "fi; "
                f"if test -f {shlex.quote(binary)}; then "
                "echo DAYZ_BINARY_OK; "
                "else "
                "echo DAYZ_BINARY_MISSING; "
                "fi"
            )

            verify_code, verify_output, verify_error = (
                self.ssh.exec(
                    verify_command
                )
            )

            combined_verify = (
                (verify_output or "")
                + "\n"
                + (verify_error or "")
            )

            manifest_ok = (
                "DAYZ_MANIFEST_OK"
                in combined_verify
            )

            binary_ok = (
                "DAYZ_BINARY_OK"
                in combined_verify
            )

            # ------------------------------------------------
            # Preserve SteamCMD's original output/error but
            # return additional verification information.
            # ------------------------------------------------

            return {
                "steamcmd_code": code,
                "steamcmd_output": output,
                "steamcmd_error": error,
                "verify_code": verify_code,
                "verify_output": verify_output or "",
                "verify_error": verify_error or "",
                "manifest_ok": manifest_ok,
                "binary_ok": binary_ok,
                "server_root": server_root,
                "appid": appid,
                "label": label,
            }

        self._append(
            (
                f"--- Installing/updating {label} "
                f"branch (app {appid}) ---"
            )
        )

        self._append(
            f"SteamCMD install directory: {server_root}"
        )

        self._append(
            "SteamCMD is starting now..."
        )

        self.jobs.start(
            task,
            on_ok=self._on_dayz_install_done,
            on_fail=self._on_dayz_install_failed,
        )

    def _on_dayz_install_done(
        self,
        result,
    ):
        steamcmd_code = result["steamcmd_code"]
        steamcmd_output = result["steamcmd_output"]
        steamcmd_error = result["steamcmd_error"]

        verify_output = result["verify_output"]
        verify_error = result["verify_error"]

        manifest_ok = result["manifest_ok"]
        binary_ok = result["binary_ok"]

        label = result["label"]
        server_root = result["server_root"]
        appid = result["appid"]

        # ----------------------------------------------------
        # SteamCMD output
        # ----------------------------------------------------

        if steamcmd_output.strip():
            self._append(
                steamcmd_output.strip()
            )

        if steamcmd_error.strip():
            self._append(
                steamcmd_error.strip()
            )

        # ----------------------------------------------------
        # Verification output
        # ----------------------------------------------------

        self._append(
            "--- Verifying DayZ installation ---"
        )

        if verify_output.strip():
            self._append(
                verify_output.strip()
            )

        if verify_error.strip():
            self._append(
                verify_error.strip()
            )

        self._append(
            f"SteamCMD exit code: {steamcmd_code}"
        )

        self._append(
            f"DayZ manifest: "
            f"{'FOUND' if manifest_ok else 'MISSING'}"
        )

        self._append(
            f"DayZ binary: "
            f"{'FOUND' if binary_ok else 'MISSING'}"
        )

        # ----------------------------------------------------
        # SUCCESS
        #
        # Require both SteamCMD success and actual DayZ files.
        # ----------------------------------------------------

        if (
            steamcmd_code == 0
            and manifest_ok
            and binary_ok
        ):
            self._append(
                f"{label.capitalize()} DayZ server "
                "installed/updated successfully."
            )

            self._append(
                f"Server root: {server_root}"
            )

            self._append(
                ""
            )

            self._set_busy(False)

            QMessageBox.information(
                self,
                "DayZ Server Installed",
                (
                    f"The {label} DayZ server was "
                    "installed/updated successfully.\n\n"
                    f"Server root:\n{server_root}\n\n"
                    f"App ID:\n{appid}\n\n"
                    "The DayZ manifest and DayZServer binary "
                    "were both found."
                ),
            )

            # Refresh status only AFTER the installation has
            # been verified.
            self.check_status()

            return

        # ----------------------------------------------------
        # FAILURE
        # ----------------------------------------------------

        self._set_busy(False)

        reasons = []

        if steamcmd_code != 0:
            reasons.append(
                f"SteamCMD exited with code {steamcmd_code}"
            )

        if not manifest_ok:
            reasons.append(
                "the Steam appmanifest was not found"
            )

        if not binary_ok:
            reasons.append(
                "DayZServer was not found"
            )

        reason_text = "\n".join(
            f"• {reason}"
            for reason in reasons
        )

        self._append(
            f"{label.capitalize()} server installation FAILED."
        )

        self._append(
            "Reasons:"
        )

        self._append(
            reason_text
        )

        QMessageBox.critical(
            self,
            "DayZ Server Installation Failed",
            (
                f"The {label} DayZ server was not "
                "successfully installed.\n\n"
                f"Server root:\n{server_root}\n\n"
                f"{reason_text}\n\n"
                "Check the log above for the SteamCMD output."
            ),
        )

    def _on_dayz_install_failed(
        self,
        error,
    ):
        self._set_busy(False)

        self._append(
            f"DayZ server installation failed: {error}"
        )

        QMessageBox.critical(
            self,
            "DayZ Installation Failed",
            str(error),
        )

    # ========================================================
    # GENERIC OPERATION FINISH
    # ========================================================

    def _on_generic_done(
        self,
        label,
        result,
    ):
        code, output, error = result

        if output:
            self._append(
                output.strip()
            )

        if error:
            self._append(
                error.strip()
            )

        self._append(
            f"{label} finished with exit code {code}.\n"
        )

        if code != 0:
            self._set_busy(False)

            QMessageBox.critical(
                self,
                "Operation Failed",
                (
                    f"{label} failed with exit code "
                    f"{code}.\n\n"
                    f"{error or output or 'Unknown error.'}"
                ),
            )

            return

        self._set_busy(False)

        self.check_status()

    # ========================================================
    # DEPLOY SYSTEMD SERVICE
    # ========================================================

    def deploy_systemd_service(self):
        if not self._require_connected():
            return

        server_root = (
            self.server_root_edit.text().strip()
        )

        if not server_root:
            QMessageBox.warning(
                self,
                "Missing server directory",
                "Enter the DayZ server installation directory first.",
            )

            return

        service_name = (
            self._systemd_service_name()
        )

        if not service_name:
            QMessageBox.warning(
                self,
                "Invalid Service Name",
                "The systemd service name is empty.",
            )

            return

        path = (
            self._systemd_service_path()
        )

        self._append(
            "--- Checking existing Systemd Service ---"
        )

        self._set_busy(True)

        def check_existing_task():
            return self._check_systemd_service()

        def check_existing_ok(
            existing,
        ):
            if existing["exists"]:
                self._display_systemd_status(
                    existing
                )

                self._set_busy(False)

                self._append(
                    (
                        f"Systemd service already exists: "
                        f"{path}"
                    )
                )

                self._append(
                    "Existing daemon was NOT modified."
                )

                if existing["valid"] is True:
                    message = (
                        "An existing Systemd service already "
                        "exists and is valid.\n\n"
                        f"Service:\n{service_name}.service\n\n"
                        f"Path:\n{path}\n\n"
                        "The existing daemon was NOT overwritten."
                    )

                    title = (
                        "Systemd Service Already Exists"
                    )

                elif existing["valid"] is False:
                    message = (
                        "An existing Systemd service already "
                        "exists, but systemd-analyze reports "
                        "that it is invalid.\n\n"
                        f"Service:\n{service_name}.service\n\n"
                        f"Path:\n{path}\n\n"
                        "The existing daemon was NOT overwritten."
                    )

                    title = (
                        "Existing Systemd Service Is Invalid"
                    )

                else:
                    message = (
                        "An existing Systemd service already "
                        "exists, but its validity could not be "
                        "confirmed.\n\n"
                        f"Service:\n{service_name}.service\n\n"
                        f"Path:\n{path}\n\n"
                        "The existing daemon was NOT overwritten."
                    )

                    title = (
                        "Existing Systemd Service"
                    )

                QMessageBox.warning(
                    self,
                    title,
                    message,
                )

                return

            self._begin_systemd_deployment(
                server_root,
                service_name,
                path,
            )

        def check_existing_fail(
            error,
        ):
            self._set_busy(False)

            self._append(
                f"Could not inspect existing Systemd service: "
                f"{error}"
            )

            QMessageBox.critical(
                self,
                "Systemd Deployment Aborted",
                (
                    "The existing systemd service could not be "
                    "checked safely.\n\n"
                    "No daemon was modified."
                ),
            )

        self.jobs.start(
            check_existing_task,
            on_ok=check_existing_ok,
            on_fail=check_existing_fail,
        )

    def _begin_systemd_deployment(
        self,
        server_root,
        service_name,
        path,
    ):
        password = self._sudo_password()

        if not password:
            self._set_busy(False)

            QMessageBox.warning(
                self,
                "Sudo Password Required",
                "Enter the sudo password on the Server Status tab first.",
            )

            return

        content = (
            self._build_systemd_unit()
        )

        if not content:
            self._set_busy(False)

            QMessageBox.warning(
                self,
                "Invalid Server Directory",
                "Enter the DayZ server installation directory first.",
            )

            return

        self._append(
            "--- Deploying New Systemd Service ---"
        )

        self._append(
            f"Service: {service_name}.service"
        )

        self._append(
            f"Path: {path}"
        )

        self._append(
            f"Server root: {server_root}"
        )

        self._append(
            "No existing daemon found."
        )

        self._append(
            "Validating new systemd unit..."
        )

        def task():
            temp_path = (
                f"/tmp/{service_name}.service"
            )

            self.ssh.write_file(
                temp_path,
                content,
                backup=False,
            )

            try:
                verify_command = (
                    "systemd-analyze verify "
                    + shlex.quote(temp_path)
                )

                code, stdout, stderr = (
                    self.ssh.exec_sudo(
                        verify_command,
                        password,
                    )
                )

                if code != 0:
                    raise RuntimeError(
                        "systemd-analyze verify failed:\n"
                        + (
                            stderr
                            or stdout
                            or "Unknown validation error"
                        )
                    )

                install_command = (
                    "if test -e "
                    + shlex.quote(path)
                    + " || test -L "
                    + shlex.quote(path)
                    + "; then "
                    "echo EXISTING_SYSTEMD_SERVICE; "
                    "exit 10; "
                    "fi; "
                    "install -m 644 "
                    + shlex.quote(temp_path)
                    + " "
                    + shlex.quote(path)
                    + " && "
                    "systemctl daemon-reload"
                )

                return self.ssh.exec_sudo(
                    "sh -c "
                    + shlex.quote(install_command),
                    password,
                )

            finally:
                try:
                    self.ssh.exec(
                        "rm -f "
                        + shlex.quote(temp_path)
                    )
                except Exception:
                    pass

        def ok(result):
            code, output, error = result

            if output:
                self._append(
                    output.strip()
                )

            if error:
                self._append(
                    error.strip()
                )

            if (
                code != 0
                and "EXISTING_SYSTEMD_SERVICE"
                in (output or "")
            ):
                self._set_busy(False)

                self._append(
                    (
                        "An existing Systemd service was "
                        "detected during installation."
                    )
                )

                self._append(
                    "Existing daemon was NOT overwritten."
                )

                QMessageBox.warning(
                    self,
                    "Systemd Service Already Exists",
                    (
                        "The service appeared while deployment "
                        "was running.\n\n"
                        f"Service:\n{service_name}.service\n\n"
                        "The existing daemon was preserved and "
                        "was NOT overwritten."
                    ),
                )

                self.check_status()

                return

            if code != 0:
                self._set_busy(False)

                self._append(
                    "Systemd service deployment failed."
                )

                QMessageBox.critical(
                    self,
                    "Systemd Deployment Failed",
                    error
                    or output
                    or "Unknown error.",
                )

                return

            self.config.systemd_service = (
                service_name
            )

            self.config.save()

            self._append(
                "Systemd service deployed successfully."
            )

            self._append(
                f"Installed: {path}"
            )

            self._append(
                "systemctl daemon-reload completed."
            )

            self._append(
                "The service was NOT started."
            )

            self._set_busy(False)

            QMessageBox.information(
                self,
                "Systemd Service Deployed",
                (
                    "The new DayZ systemd service was "
                    "verified, installed, and daemon-reloaded.\n\n"
                    f"Service:\n{service_name}.service\n\n"
                    f"Path:\n{path}\n\n"
                    "The service has NOT been started.\n\n"
                    "No existing daemon was overwritten."
                ),
            )

            self.check_status()

        def fail(error):
            self._set_busy(False)

            self._append(
                f"Systemd deployment failed: {error}"
            )

            QMessageBox.critical(
                self,
                "Systemd Deployment Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=ok,
            on_fail=fail,
        )

    # ========================================================
    # DEPLOY / UNINSTALL DZMANAGER.PBO
    # ========================================================

    def _set_pbo_button_state(self, deployed):
        self._pbo_deployed = bool(
            deployed
        )

        self.deploy_pbo_btn.setText(
            "Uninstall dzmanager.pbo"
            if self._pbo_deployed
            else "Deploy dzmanager.pbo"
        )

    def _check_pbo_deployed(self, remote_path):
        """
        Return True if dzmanager.pbo is confirmed present at
        remote_path, False if confirmed absent. Meant to be called
        from a background thread. Any other failure (dropped
        connection, permissions, missing addons dir, etc.) is NOT
        caught here -- it propagates so the caller can tell "confirmed
        absent" apart from "couldn't check" instead of assuming
        the file is gone.
        """

        sftp = self.ssh.sftp()

        try:
            try:
                sftp.stat(remote_path)

                return True

            except FileNotFoundError:
                return False

        finally:
            sftp.close()

    def _remote_addon_paths(self, server_root):
        remote_dir = (
            server_root.rstrip("/")
            + "/"
            + REMOTE_ADDON_SUBDIR
        )

        remote_path = (
            remote_dir
            + "/"
            + DZMANAGER_PBO_FILENAME
        )

        return remote_dir, remote_path

    def deploy_dzmanager_pbo(self):
        if not self._require_connected():
            return

        server_root = (
            self.server_root_edit.text().strip()
        )

        if not server_root:
            QMessageBox.warning(
                self,
                "Missing server directory",
                "Enter the DayZ server installation directory first.",
            )

            return

        remote_dir, remote_path = (
            self._remote_addon_paths(server_root)
        )

        if self._pbo_deployed:
            self._uninstall_dzmanager_pbo(
                remote_path
            )

            return

        local_path = (
            LOCAL_PBO_DIR
            / DZMANAGER_PBO_FILENAME
        )

        if not local_path.is_file():
            QMessageBox.critical(
                self,
                "dzmanager.pbo Not Found",
                (
                    "Could not find dzmanager.pbo locally:\n\n"
                    f"{local_path}"
                ),
            )

            return

        self._append(
            "--- Deploying dzmanager.pbo ---"
        )

        self._append(
            f"Local:  {local_path}"
        )

        self._append(
            f"Remote: {remote_path}"
        )

        self._set_busy(True)

        def task():
            sftp = self.ssh.sftp()

            try:
                try:
                    sftp.stat(remote_dir)

                except FileNotFoundError:
                    raise RuntimeError(
                        "Remote addons directory not found:\n"
                        f"{remote_dir}\n\n"
                        "Is the DayZ server deployed at this "
                        "install directory?"
                    )

                sftp.put(
                    str(local_path),
                    remote_path,
                )

            finally:
                sftp.close()

            return remote_path

        def ok(result):
            self._set_busy(False)

            self._set_pbo_button_state(
                True
            )

            self._append(
                f"dzmanager.pbo deployed to: {result}"
            )

            QMessageBox.information(
                self,
                "dzmanager.pbo Deployed",
                f"Deployed:\n{result}",
            )

        def fail(error):
            self._set_busy(False)

            self._append(
                f"dzmanager.pbo deployment failed: {error}"
            )

            QMessageBox.critical(
                self,
                "Deployment Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=ok,
            on_fail=fail,
        )

    def _uninstall_dzmanager_pbo(self, remote_path):
        self._append(
            "--- Uninstalling dzmanager.pbo ---"
        )

        self._append(
            f"Removing: {remote_path}"
        )

        self._set_busy(True)

        def task():
            return self.ssh.exec(
                "rm -f "
                + shlex.quote(remote_path)
            )

        def ok(result):
            code, output, error = result

            self._set_busy(False)

            if output:
                self._append(
                    output.strip()
                )

            if error:
                self._append(
                    error.strip()
                )

            if code != 0:
                self._append(
                    "Failed to remove dzmanager.pbo."
                )

                QMessageBox.critical(
                    self,
                    "Uninstall Failed",
                    error
                    or output
                    or "Unknown error.",
                )

                return

            self._set_pbo_button_state(
                False
            )

            self._append(
                "dzmanager.pbo removed."
            )

            QMessageBox.information(
                self,
                "dzmanager.pbo Removed",
                f"Removed:\n{remote_path}",
            )

        def fail(error):
            self._set_busy(False)

            self._append(
                f"dzmanager.pbo uninstall failed: {error}"
            )

            QMessageBox.critical(
                self,
                "Uninstall Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=ok,
            on_fail=fail,
        )

    # ========================================================
    # APPLY SETTINGS
    # ========================================================

    def apply_to_settings(self):
        if not self._require_connected():
            return

        steamcmd_path = (
            self.steamcmd_path_edit.text().strip()
            or "steamcmd"
        )

        server_root = (
            self.server_root_edit.text().strip()
        )

        steam_user = (
            self.steam_user_edit.text().strip()
            or "anonymous"
        )

        if not server_root:
            QMessageBox.warning(
                self,
                "Missing server directory",
                "Enter the DayZ server installation directory.",
            )

            return

        self.config.server_root = (
            server_root
        )

        self.config.steamcmd_path = (
            steamcmd_path
        )

        self.config.steam_user = (
            steam_user
        )

        self.config.update_server_paths()

        self.config.save()

        self._append(
            "--- Deployment paths applied ---"
        )

        self._append(
            f"Server root: {self.config.server_root}"
        )

        self._append(
            f"Profiles: {self.config.profiles_dir}"
        )

        self._append(
            f"Keys: {self.config.keys_dir}"
        )

        self._append(
            f"MPMissions: {self.config.mpmissions_dir}"
        )

        self._append(
            f"Workshop: {self.config.workshop_content_dir}"
        )

        self._append("")

        QMessageBox.information(
            self,
            "Applied",
            (
                "Deployment paths saved.\n\n"
                f"Server root:\n{self.config.server_root}\n\n"
                f"Keys:\n{self.config.keys_dir}\n\n"
                f"MPMissions:\n{self.config.mpmissions_dir}\n\n"
                f"Workshop:\n{self.config.workshop_content_dir}\n\n"
                "The Profiles / Logs path is controlled by "
                "the Systemd -profiles= setting."
            ),
        )

    # ========================================================
    # SHUTDOWN
    # ========================================================

    def shutdown(self):
        self.jobs.shutdown()
