from datetime import datetime
import posixpath
import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from rcon_client import (
    BattlEyeRConClient,
    BattlEyeRConAuthenticationError,
    BattlEyeRConTimeout,
)

from worker import WorkerRegistry


class CommandLineEdit(QLineEdit):
    """
    QLineEdit with command history.

    Up   -> previous command
    Down -> next command
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.history = []
        self.history_index = 0

    def add_history(self, command):
        command = command.strip()

        if not command:
            return

        if not self.history or self.history[-1] != command:
            self.history.append(command)

        self.history_index = len(self.history)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Up:
            if not self.history:
                return

            self.history_index = max(
                0,
                self.history_index - 1,
            )

            self.setText(
                self.history[self.history_index]
            )

            self.setCursorPosition(
                len(self.text())
            )

            return

        if event.key() == Qt.Key_Down:
            if not self.history:
                return

            self.history_index = min(
                len(self.history),
                self.history_index + 1,
            )

            if self.history_index >= len(self.history):
                self.clear()
            else:
                self.setText(
                    self.history[self.history_index]
                )

            return

        super().keyPressEvent(event)


class RConPanel(QWidget):
    """
    BattlEye RCon administration panel for a Linux DayZ server.

    RCon itself is independent from SSH.

    SSH is only used to inspect/create/repair the remote BattlEye
    configuration.

    Linux BattlEye directory:

        <server_root>/battleye/

    Normal config:

        beserver_x64.cfg

    Active config after BattlEye starts:

        beserver_x64_active_<random>.cfg

    The panel searches for active configs FIRST.

    The RCon port is editable in the UI.

    If no remote config is available, port 2305 is used as the
    default fallback.

    Setup / Repair RCon updates the active configuration as well
    as the normal beserver_x64.cfg seed configuration.

    RestrictRCon is set to 0 so administrative commands are allowed.
    """

    RECONNECT_INTERVAL_MS = 5000

    # Standard DayZ game port is normally 2302.
    # BattlEye RCon must use a different port.
    DEFAULT_RCON_PORT = 2305

    # Old versions of the manager used 2302 for RCon.
    LEGACY_RCON_PORT = 2302

    # Linux BattlEye directory.
    BATTLEYE_DIRECTORY_NAME = "battleye"

    # Normal BattlEye configuration filenames.
    CONFIG_FILENAMES = (
        "beserver_x64.cfg",
        "beserver.cfg",
        "BEServer_x64.cfg",
        "BEServer.cfg",
    )

    # Active BattlEye configuration filename pattern.
    ACTIVE_CONFIG_PATTERN = re.compile(
        r"^beserver(?:_x64)?_active_[^/]+\.cfg$",
        re.IGNORECASE,
    )

    # Qt signals used to safely move RCon receiver-thread callbacks
    # into the GUI thread.
    rcon_message_signal = Signal(str)
    rcon_disconnect_signal = Signal()
    rcon_error_signal = Signal(str)

    def __init__(
        self,
        ssh,
        config,
    ):
        super().__init__()

        self.ssh = ssh
        self.config = config

        self.jobs = WorkerRegistry()

        self.rcon = None

        self._connecting = False
        self._manual_disconnect = False

        self._detected_port = self.DEFAULT_RCON_PORT
        self._detected_config_path = None

        self._build_ui()

        # ----------------------------------------------------------
        # Receiver thread -> GUI thread signals
        # ----------------------------------------------------------

        self.rcon_message_signal.connect(
            self._append_response
        )

        self.rcon_disconnect_signal.connect(
            self._handle_connection_lost
        )

        self.rcon_error_signal.connect(
            self._append_error
        )

        # ----------------------------------------------------------
        # Load settings BEFORE starting reconnect timer.
        # ----------------------------------------------------------

        self._load_config()

        # ----------------------------------------------------------
        # Reconnect timer
        # ----------------------------------------------------------

        self._reconnect_timer = QTimer(self)

        self._reconnect_timer.setInterval(
            self.RECONNECT_INTERVAL_MS
        )

        self._reconnect_timer.timeout.connect(
            self._auto_reconnect_tick
        )

        self._reconnect_timer.start()

        # Check remote config when panel opens.
        self.refresh_remote_config()

    # ==============================================================
    # UI
    # ==============================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ----------------------------------------------------------
        # CONNECTION ROW
        # ----------------------------------------------------------

        connection_row = QHBoxLayout()

        self.status_label = QLabel(
            "● Disconnected"
        )

        # Start disconnected.
        self._set_status_indicator(
            "disconnected"
        )

        connection_row.addWidget(
            self.status_label
        )

        connection_row.addSpacing(15)

        connection_row.addWidget(
            QLabel("Host:")
        )

        self.host_edit = QLineEdit()

        self.host_edit.setPlaceholderText(
            "RCon host"
        )

        connection_row.addWidget(
            self.host_edit,
            1,
        )

        connection_row.addWidget(
            QLabel("Port:")
        )

        self.port_edit = QLineEdit()

        # Port is manually editable.
        #
        # IMPORTANT:
        # No setAlignment(Qt.AlignCenter) here.
        # QLineEdit therefore uses the normal left alignment,
        # just like the Host field.
        self.port_edit.setReadOnly(
            False
        )

        self.port_edit.setMaximumWidth(
            110
        )

        self.port_edit.setPlaceholderText(
            "2305"
        )

        self.port_edit.setToolTip(
            "BattlEye RCon UDP port. "
            "The panel detects the remote port automatically, "
            "but you can manually change it here."
        )

        # When the user manually changes the port, keep the
        # internal detected value synchronized.
        self.port_edit.editingFinished.connect(
            self._port_edit_finished
        )

        connection_row.addWidget(
            self.port_edit
        )

        connection_row.addWidget(
            QLabel("Password:")
        )

        self.password_edit = QLineEdit()

        self.password_edit.setEchoMode(
            QLineEdit.Password
        )

        self.password_edit.setPlaceholderText(
            "RCon password"
        )

        connection_row.addWidget(
            self.password_edit,
            1,
        )

        self.setup_button = QPushButton(
            "Setup / Repair RCon"
        )

        self.setup_button.setToolTip(
            "Create or repair the Linux BattlEye RCon "
            "configuration in the lowercase battleye folder."
        )

        self.setup_button.clicked.connect(
            self.setup_rcon
        )

        connection_row.addWidget(
            self.setup_button
        )

        self.connect_button = QPushButton(
            "Connect"
        )

        self.connect_button.clicked.connect(
            self.toggle_connection
        )

        connection_row.addWidget(
            self.connect_button
        )

        self.auto_reconnect_checkbox = QCheckBox(
            "Auto reconnect"
        )

        connection_row.addWidget(
            self.auto_reconnect_checkbox
        )

        layout.addLayout(
            connection_row
        )

        # ----------------------------------------------------------
        # CONFIG STATUS
        # ----------------------------------------------------------

        self.config_status_label = QLabel(
            "BattlEye config: not checked"
        )

        self.config_status_label.setStyleSheet(
            "color: gray;"
        )

        layout.addWidget(
            self.config_status_label
        )

        # ----------------------------------------------------------
        # CONSOLE
        # ----------------------------------------------------------

        self.console = QPlainTextEdit()

        self.console.setReadOnly(
            True
        )

        font = QFont(
            "Monospace"
        )

        font.setStyleHint(
            QFont.TypeWriter
        )

        self.console.setFont(
            font
        )

        layout.addWidget(
            self.console,
            1,
        )

        # ----------------------------------------------------------
        # QUICK COMMANDS
        # ----------------------------------------------------------

        quick_row = QHBoxLayout()

        quick_row.addWidget(
            QLabel("Quick commands:")
        )

        players_button = QPushButton(
            "#players"
        )

        players_button.clicked.connect(
            lambda: self._send_command(
                "players"
            )
        )

        quick_row.addWidget(
            players_button
        )

        say_button = QPushButton(
            "#say"
        )

        say_button.clicked.connect(
            self._quick_say
        )

        quick_row.addWidget(
            say_button
        )

        kick_button = QPushButton(
            "#kick"
        )

        kick_button.clicked.connect(
            self._quick_kick
        )

        quick_row.addWidget(
            kick_button
        )

        ban_button = QPushButton(
            "#ban"
        )

        ban_button.clicked.connect(
            self._quick_ban
        )

        quick_row.addWidget(
            ban_button
        )

        shutdown_button = QPushButton(
            "#shutdown"
        )

        shutdown_button.clicked.connect(
            self._quick_shutdown
        )

        quick_row.addWidget(
            shutdown_button
        )

        quick_row.addStretch()

        clear_button = QPushButton(
            "Clear console"
        )

        clear_button.clicked.connect(
            self.console.clear
        )

        quick_row.addWidget(
            clear_button
        )

        layout.addLayout(
            quick_row
        )

        # ----------------------------------------------------------
        # COMMAND INPUT
        # ----------------------------------------------------------

        command_row = QHBoxLayout()

        self.command_edit = CommandLineEdit()

        self.command_edit.setPlaceholderText(
            "Enter BattlEye RCon command..."
        )

        self.command_edit.returnPressed.connect(
            self.send_current_command
        )

        command_row.addWidget(
            self.command_edit,
            1,
        )

        self.send_button = QPushButton(
            "Send"
        )

        self.send_button.clicked.connect(
            self.send_current_command
        )

        command_row.addWidget(
            self.send_button
        )

        layout.addLayout(
            command_row
        )

        self._update_connection_ui(
            False
        )

    # ==============================================================
    # STATUS INDICATOR
    # ==============================================================

    def _set_status_indicator(self, state):
        """
        Update the connection indicator.

        States:

            disconnected -> red
            connecting   -> orange
            connected    -> green
        """

        status_styles = {
            "disconnected": (
                "color: #e74c3c;"
            ),
            "connecting": (
                "color: #f39c12;"
            ),
            "connected": (
                "color: #2ecc71;"
            ),
        }

        status_text = {
            "disconnected": "● Disconnected",
            "connecting": "● Connecting...",
            "connected": "● Connected",
        }

        if state not in status_styles:
            state = "disconnected"

        self.status_label.setText(
            status_text[state]
        )

        self.status_label.setStyleSheet(
            status_styles[state]
        )

    # ==============================================================
    # CONFIG
    # ==============================================================

    def _load_config(self):
        host = getattr(
            self.config,
            "rcon_host",
            "",
        )

        if not host:
            host = getattr(
                self.config,
                "host",
                "",
            )

        self.host_edit.setText(
            host
        )

        configured_port = getattr(
            self.config,
            "rcon_port",
            self.DEFAULT_RCON_PORT,
        )

        try:
            configured_port = int(
                configured_port
            )
        except (
            TypeError,
            ValueError,
        ):
            configured_port = self.DEFAULT_RCON_PORT

        if not 1 <= configured_port <= 65535:
            configured_port = self.DEFAULT_RCON_PORT

        self._set_detected_port(
            configured_port,
            f"Configured RCon port: {configured_port}",
        )

        self.password_edit.setText(
            getattr(
                self.config,
                "rcon_password",
                "",
            )
        )

        # Default OFF if no setting exists.
        self.auto_reconnect_checkbox.setChecked(
            bool(
                getattr(
                    self.config,
                    "rcon_auto_reconnect",
                    False,
                )
            )
        )

    def _save_config(self):
        self.config.rcon_host = (
            self.host_edit.text().strip()
        )

        # Save the port currently shown in the editable box.
        port = self._get_port_from_edit()

        if port is None:
            port = self.DEFAULT_RCON_PORT

        self.config.rcon_port = port

        self.config.rcon_password = (
            self.password_edit.text()
        )

        self.config.rcon_auto_reconnect = (
            self.auto_reconnect_checkbox.isChecked()
        )

        self.config.save()

    # ==============================================================
    # PORT EDITING
    # ==============================================================

    def _get_port_from_edit(self):
        """
        Return the port currently entered in the port box.

        Returns None when the value is invalid.
        """

        text = (
            self.port_edit.text().strip()
        )

        if not text:
            return None

        try:
            port = int(
                text
            )

        except (
            TypeError,
            ValueError,
        ):
            return None

        if not 1 <= port <= 65535:
            return None

        return port

    def _port_edit_finished(self):
        """
        Validate and store a manually entered RCon port.
        """

        port = self._get_port_from_edit()

        if port is None:
            QMessageBox.warning(
                self,
                "Invalid RCon port",
                "Please enter a valid UDP port between "
                "1 and 65535.",
            )

            self.port_edit.setText(
                str(
                    self._detected_port
                )
            )

            return

        self._detected_port = port

        self.port_edit.setToolTip(
            f"RCon port {port}. "
            "Manually entered."
        )

    # ==============================================================
    # REMOTE BATTLEYE CONFIG
    # ==============================================================

    def _get_battleye_directory(self):
        server_root = str(
            getattr(
                self.config,
                "server_root",
                "",
            )
        ).strip()

        if not server_root:
            raise RuntimeError(
                "Server root is not configured."
            )

        return posixpath.join(
            server_root,
            self.BATTLEYE_DIRECTORY_NAME,
        )

    def _get_config_candidates(self):
        battleye_dir = (
            self._get_battleye_directory()
        )

        return [
            posixpath.join(
                battleye_dir,
                filename,
            )
            for filename in self.CONFIG_FILENAMES
        ]

    def _list_remote_battleye_files(self):
        """
        List files in the remote battleye directory using SFTP.
        """

        battleye_dir = (
            self._get_battleye_directory()
        )

        sftp = self.ssh.sftp()

        return list(
            sftp.listdir(
                battleye_dir
            )
        )

    def _find_active_config(self):
        """
        Find the active BattlEye configuration.

        Example:

            battleye/beserver_x64_active_57110bc1.cfg
        """

        battleye_dir = (
            self._get_battleye_directory()
        )

        try:
            filenames = (
                self._list_remote_battleye_files()
            )

        except Exception as error:
            raise FileNotFoundError(
                "Could not list the remote BattlEye directory: "
                f"{error}"
            ) from error

        active_paths = []

        for filename in filenames:
            filename = str(
                filename
            ).strip()

            if not filename:
                continue

            if not self.ACTIVE_CONFIG_PATTERN.match(
                filename
            ):
                continue

            active_paths.append(
                posixpath.join(
                    battleye_dir,
                    filename,
                )
            )

        if not active_paths:
            raise FileNotFoundError(
                "No active BattlEye config was found in "
                f"{battleye_dir}."
            )

        # Prefer the newest active configuration.
        try:
            sftp = self.ssh.sftp()

            active_paths_with_mtime = []

            for path in active_paths:
                try:
                    stat = sftp.stat(
                        path
                    )

                    active_paths_with_mtime.append(
                        (
                            float(
                                stat.st_mtime
                            ),
                            path,
                        )
                    )

                except Exception:
                    active_paths_with_mtime.append(
                        (
                            0.0,
                            path,
                        )
                    )

            active_paths_with_mtime.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            active_paths = [
                item[1]
                for item in active_paths_with_mtime
            ]

        except Exception:
            active_paths.sort(
                key=str.lower,
                reverse=True,
            )

        for path in active_paths:
            try:
                text = self.ssh.read_file(
                    path
                )

                return path, text

            except Exception:
                continue

        raise FileNotFoundError(
            "Active BattlEye config files were found, "
            "but none could be read."
        )

    @staticmethod
    def _parse_rcon_port(text):
        pattern = re.compile(
            r"^\s*RConPort\s*(?:=|\s)\s*(\d+)"
            r"(?:\s*(?:#.*)?)?$",
            re.IGNORECASE,
        )

        for line in text.splitlines():
            match = pattern.match(
                line
            )

            if not match:
                continue

            try:
                port = int(
                    match.group(1)
                )

            except ValueError:
                continue

            if 1 <= port <= 65535:
                return port

        return None

    @staticmethod
    def _parse_rcon_password(text):
        pattern = re.compile(
            r"^\s*RConPassword\s*(?:=|\s)\s*(.*?)\s*$",
            re.IGNORECASE,
        )

        for line in text.splitlines():
            match = pattern.match(
                line
            )

            if not match:
                continue

            return match.group(1).strip()

        return None

    @staticmethod
    def _upsert_config_value(
        text,
        key,
        value,
    ):
        pattern = re.compile(
            rf"^\s*{re.escape(key)}\s*(?:=|\s)\s*.*$",
            re.IGNORECASE,
        )

        lines = text.splitlines()

        replaced = False
        output = []

        for line in lines:
            if pattern.match(line):
                output.append(
                    f"{key} {value}"
                )

                replaced = True

            else:
                output.append(
                    line
                )

        if not replaced:
            if output and output[-1].strip():
                output.append("")

            output.append(
                f"{key} {value}"
            )

        return "\n".join(
            output
        ) + "\n"

    def _find_remote_battleye_config(self):
        """
        Search order:

        1. beserver_x64_active_*.cfg
        2. beserver_active_*.cfg
        3. beserver_x64.cfg
        4. beserver.cfg
        5. case variants
        """

        # Active config FIRST.
        try:
            return self._find_active_config()

        except Exception:
            pass

        # Normal config files.
        candidates = (
            self._get_config_candidates()
        )

        for path in candidates:
            try:
                text = self.ssh.read_file(
                    path
                )

                return path, text

            except Exception:
                continue

        raise FileNotFoundError(
            "No BattlEye config was found in "
            f"{self._get_battleye_directory()}."
        )

    def _find_all_remote_battleye_configs(self):
        """
        Find every BattlEye config that currently exists.

        Returns:

            [
                (path, text, active),
                ...
            ]
        """

        battleye_dir = (
            self._get_battleye_directory()
        )

        results = []
        seen = set()

        # Fixed config files.
        for path in self._get_config_candidates():
            if path in seen:
                continue

            try:
                text = self.ssh.read_file(
                    path
                )

            except Exception:
                continue

            results.append(
                (
                    path,
                    text,
                    False,
                )
            )

            seen.add(
                path
            )

        # Active config files.
        try:
            filenames = (
                self._list_remote_battleye_files()
            )

        except Exception:
            filenames = []

        for filename in filenames:
            filename = str(
                filename
            ).strip()

            if not filename:
                continue

            if not self.ACTIVE_CONFIG_PATTERN.match(
                filename
            ):
                continue

            path = posixpath.join(
                battleye_dir,
                filename,
            )

            if path in seen:
                continue

            try:
                text = self.ssh.read_file(
                    path
                )

            except Exception:
                continue

            results.append(
                (
                    path,
                    text,
                    True,
                )
            )

            seen.add(
                path
            )

        # Active configs first.
        results.sort(
            key=lambda item: (
                not item[2],
                item[0].lower(),
            )
        )

        return results

    def _read_remote_rcon_config(self):
        """
        Read the remote BattlEye config.
        """

        try:
            path, text = (
                self._find_remote_battleye_config()
            )

            port = self._parse_rcon_port(
                text
            )

            if port is None:
                port = self.DEFAULT_RCON_PORT

            password = self._parse_rcon_password(
                text
            )

            active = (
                "_active_" in
                posixpath.basename(
                    path
                ).lower()
            )

            return {
                "path": path,
                "text": text,
                "port": port,
                "password": password,
                "exists": True,
                "active": active,
            }

        except FileNotFoundError:
            return {
                "path": None,
                "text": "",
                "port": self.DEFAULT_RCON_PORT,
                "password": None,
                "exists": False,
                "active": False,
            }

    def refresh_remote_config(self):
        """
        Check the remote BattlEye config.

        If SSH is not connected, do not claim the config is missing.
        """

        if self._connecting:
            return

        if not self.ssh.is_connected():
            self.config_status_label.setText(
                "BattlEye config: SSH not connected "
                "(manual RCon port can still be used)"
            )

            return

        self.config_status_label.setText(
            "BattlEye config: checking..."
        )

        self.jobs.start(
            self._read_remote_rcon_config,
            on_ok=self._on_remote_config_checked,
            on_fail=self._on_remote_config_check_failed,
        )

    def _on_remote_config_checked(self, result):
        port = int(
            result.get(
                "port",
                self.DEFAULT_RCON_PORT,
            )
        )

        path = result.get(
            "path"
        )

        exists = bool(
            result.get(
                "exists",
                False,
            )
        )

        active = bool(
            result.get(
                "active",
                False,
            )
        )

        self._detected_config_path = path

        if exists and path:
            self._set_detected_port(
                port,
                f"Detected from {path}",
            )

            if active:
                self.config_status_label.setText(
                    f"Active BattlEye config: "
                    f"{path} "
                    f"(RConPort {port})"
                )

            else:
                self.config_status_label.setText(
                    f"BattlEye config: "
                    f"{path} "
                    f"(RConPort {port})"
                )

        else:
            # Do not overwrite a manually entered valid port when
            # no remote config was found.
            manual_port = (
                self._get_port_from_edit()
            )

            if manual_port is not None:
                self._detected_port = manual_port

                self.config_status_label.setText(
                    "BattlEye config: not found "
                    f"(using manually entered UDP {manual_port})"
                )

            else:
                self._set_detected_port(
                    self.DEFAULT_RCON_PORT,
                    "Fallback: UDP 2305",
                )

                self.config_status_label.setText(
                    "BattlEye config: not found "
                    "(using fallback UDP 2305)"
                )

    def _on_remote_config_check_failed(self, error):
        manual_port = (
            self._get_port_from_edit()
        )

        if manual_port is None:
            self._set_detected_port(
                self.DEFAULT_RCON_PORT,
                "Fallback: UDP 2305",
            )

        self.config_status_label.setText(
            "BattlEye config: could not be checked"
        )

        self._append_error(
            f"Could not inspect BattlEye config: {error}"
        )

    def _set_detected_port(
        self,
        port,
        source=None,
    ):
        try:
            port = int(
                port
            )

        except (
            TypeError,
            ValueError,
        ):
            port = self.DEFAULT_RCON_PORT

        if not 1 <= port <= 65535:
            port = self.DEFAULT_RCON_PORT

        self._detected_port = port

        self.port_edit.setText(
            str(port)
        )

        if source:
            self.port_edit.setToolTip(
                f"RCon port {port}. {source}. "
                "You can edit this value manually."
            )

        else:
            self.port_edit.setToolTip(
                f"RCon port {port}. "
                "You can edit this value manually."
            )

    # ==============================================================
    # SETUP / REPAIR
    # ==============================================================

    def setup_rcon(self):
        """
        Create or repair BattlEye RCon configuration.
        """

        host = (
            self.host_edit.text().strip()
        )

        password = (
            self.password_edit.text()
        )

        port = (
            self._get_port_from_edit()
        )

        if not host:
            self._append_error(
                "RCon host is empty."
            )
            return

        if port is None:
            self._append_error(
                "RCon port is invalid. "
                "Enter a number between 1 and 65535."
            )
            return

        if not password:
            self._append_error(
                "RCon password is empty."
            )
            return

        if len(password) > 32:
            self._append_error(
                "RCon password must be 32 characters or fewer."
            )
            return

        if any(
            character.isspace()
            for character in password
        ):
            self._append_error(
                "RCon password cannot contain spaces or whitespace."
            )
            return

        if not self.ssh.is_connected():
            self._append_error(
                "SSH must be connected before BattlEye "
                "RCon configuration can be created or repaired."
            )

            self.config_status_label.setText(
                "BattlEye config: SSH connection required"
            )

            return

        # Save the manually entered port.
        self._detected_port = port

        self._save_config()

        self.setup_button.setEnabled(
            False
        )

        self.config_status_label.setText(
            "BattlEye config: setting up..."
        )

        self._append_system(
            "Setting up BattlEye RCon configuration..."
        )

        self._append_system(
            "Using Linux BattlEye directory: battleye"
        )

        self._append_system(
            f"Requested RCon port: {port}"
        )

        self.jobs.start(
            self._setup_remote_rcon_config,
            on_ok=self._on_setup_rcon_ok,
            on_fail=self._on_setup_rcon_failed,
        )

    def _setup_remote_rcon_config(self):
        battleye_dir = (
            self._get_battleye_directory()
        )

        self.ssh.exec(
            f"mkdir -p "
            f"{self._shell_quote(battleye_dir)}"
        )

        existing_configs = (
            self._find_all_remote_battleye_configs()
        )

        # Active config wins.
        primary_path = None
        primary_text = ""

        for (
            path,
            text,
            active,
        ) in existing_configs:

            if active:
                primary_path = path
                primary_text = text
                break

        if primary_path is None:
            for (
                path,
                text,
                active,
            ) in existing_configs:

                primary_path = path
                primary_text = text
                break

        # Nothing exists: create the standard x64 config.
        if primary_path is None:
            primary_path = posixpath.join(
                battleye_dir,
                "beserver_x64.cfg",
            )

            primary_text = ""

        # ----------------------------------------------------------
        # IMPORTANT:
        #
        # The USER-EDITED port box is now the source of truth.
        # ----------------------------------------------------------

        port = self._get_port_from_edit()

        if port is None:
            port = self.DEFAULT_RCON_PORT

        existing_port = (
            self._parse_rcon_port(
                primary_text
            )
        )

        migrated_legacy_port = (
            existing_port == self.LEGACY_RCON_PORT
            and port == self.DEFAULT_RCON_PORT
        )

        # Build repaired primary configuration.
        new_text = self._upsert_config_value(
            primary_text,
            "RConPassword",
            self.password_edit.text(),
        )

        new_text = self._upsert_config_value(
            new_text,
            "RConPort",
            port,
        )

        new_text = self._upsert_config_value(
            new_text,
            "RestrictRCon",
            0,
        )

        # Always maintain the normal x64 seed config.
        seed_path = posixpath.join(
            battleye_dir,
            "beserver_x64.cfg",
        )

        targets = {
            seed_path: new_text,
        }

        # Update every existing BattlEye config.
        for (
            path,
            text,
            active,
        ) in existing_configs:

            updated = self._upsert_config_value(
                text,
                "RConPassword",
                self.password_edit.text(),
            )

            updated = self._upsert_config_value(
                updated,
                "RConPort",
                port,
            )

            updated = self._upsert_config_value(
                updated,
                "RestrictRCon",
                0,
            )

            targets[
                path
            ] = updated

        # Write all config files.
        for path, text in targets.items():
            self.ssh.write_file(
                path,
                text,
            )

        active_paths = [
            path
            for (
                path,
                text,
                active,
            ) in existing_configs
            if active
        ]

        return {
            "path": primary_path,
            "seed_path": seed_path,
            "active_paths": active_paths,
            "port": port,
            "migrated_legacy_port": migrated_legacy_port,
        }

    @staticmethod
    def _shell_quote(value):
        value = str(
            value
        )

        return "'" + value.replace(
            "'",
            "'\"'\"'",
        ) + "'"

    def _on_setup_rcon_ok(self, result):
        self.setup_button.setEnabled(
            True
        )

        path = result.get(
            "path"
        )

        seed_path = result.get(
            "seed_path"
        )

        active_paths = result.get(
            "active_paths",
            [],
        )

        port = int(
            result.get(
                "port",
                self.DEFAULT_RCON_PORT,
            )
        )

        migrated = bool(
            result.get(
                "migrated_legacy_port",
                False,
            )
        )

        self._detected_config_path = path

        self._set_detected_port(
            port,
            f"Configured in {path}",
        )

        if active_paths:
            active_text = (
                active_paths[0]
            )

            self.config_status_label.setText(
                f"Active BattlEye config repaired: "
                f"{active_text} "
                f"(RConPort {port})"
            )

        else:
            self.config_status_label.setText(
                f"BattlEye config ready: "
                f"{seed_path} "
                f"(RConPort {port})"
            )

        self._append_system(
            "BattlEye RCon configuration created/repaired."
        )

        self._append_system(
            f"RCon port: {port}"
        )

        if migrated:
            self._append_system(
                "Migrated old RConPort 2302 to 2305."
            )

        self._append_system(
            f"RCon seed config: {seed_path}"
        )

        if active_paths:
            for active_path in active_paths:
                self._append_system(
                    f"Updated active config: {active_path}"
                )

        self._append_system(
            "RestrictRCon: 0"
        )

        self._append_system(
            "RCon password updated on the server."
        )

        self._append_system(
            "Restart the DayZ server so BattlEye loads "
            "the repaired configuration."
        )

    def _on_setup_rcon_failed(self, error):
        self.setup_button.setEnabled(
            True
        )

        self.config_status_label.setText(
            "BattlEye config: setup failed"
        )

        self._append_error(
            f"BattlEye RCon setup failed: {error}"
        )

    # ==============================================================
    # CONNECTION
    # ==============================================================

    @property
    def connected(self):
        return (
            self.rcon is not None
            and self.rcon.connected
        )

    def toggle_connection(self):
        if self.connected:
            self.disconnect()

        else:
            self.connect()

    def connect(self):
        if self._connecting:
            return

        host = (
            self.host_edit.text().strip()
        )

        password = (
            self.password_edit.text()
        )

        port = (
            self._get_port_from_edit()
        )

        if not host:
            self._append_error(
                "RCon host is empty."
            )
            return

        if port is None:
            self._append_error(
                "RCon port is invalid. "
                "Enter a number between 1 and 65535."
            )
            return

        if not password:
            self._append_error(
                "RCon password is empty."
            )
            return

        if len(password) > 32:
            self._append_error(
                "RCon password must be 32 characters or fewer."
            )
            return

        if any(
            character.isspace()
            for character in password
        ):
            self._append_error(
                "RCon password cannot contain spaces or whitespace."
            )
            return

        # Save the current manually selected port.
        self._detected_port = port

        self._save_config()

        self._manual_disconnect = False
        self._connecting = True

        self._update_connection_ui(
            False,
            connecting=True,
        )

        self._append_system(
            "Checking remote BattlEye RCon port..."
        )

        self.jobs.start(
            self._prepare_connection,
            on_ok=self._on_connection_prepared,
            on_fail=self._on_connect_failed,
        )

    def _prepare_connection(self):
        """
        Inspect the remote BattlEye configuration first.

        IMPORTANT:
        If the user manually entered a port, that port is used.

        The remote config is still inspected so the active config
        can be displayed, but it does not silently overwrite the
        port the user entered.
        """

        host = (
            self.host_edit.text().strip()
        )

        password = (
            self.password_edit.text()
        )

        manual_port = (
            self._get_port_from_edit()
        )

        if manual_port is None:
            manual_port = self.DEFAULT_RCON_PORT

        config_result = None

        if self.ssh.is_connected():
            config_result = (
                self._read_remote_rcon_config()
            )

        if config_result is None:
            config_result = {
                "path": None,
                "port": manual_port,
                "password": None,
                "exists": False,
                "active": False,
            }

        # The editable UI port is authoritative.
        port = manual_port

        return {
            "host": host,
            "port": port,
            "password": password,
            "config_path": config_result.get(
                "path"
            ),
            "config_exists": config_result.get(
                "exists",
                False,
            ),
            "config_active": config_result.get(
                "active",
                False,
            ),
            "remote_port": config_result.get(
                "port"
            ),
        }

    def _on_connection_prepared(self, result):
        if not self._connecting:
            return

        host = result["host"]

        port = int(
            result["port"]
        )

        password = result["password"]

        config_path = result.get(
            "config_path"
        )

        config_exists = result.get(
            "config_exists",
            False,
        )

        config_active = result.get(
            "config_active",
            False,
        )

        remote_port = result.get(
            "remote_port"
        )

        self._detected_config_path = (
            config_path
        )

        # Keep the manually selected port in the UI.
        self._set_detected_port(
            port,
            "Selected in RCon panel",
        )

        if config_exists and config_path:
            self.config_status_label.setText(
                f"BattlEye config: "
                f"{config_path} "
                f"(remote RConPort {remote_port}, "
                f"panel port {port})"
            )

        else:
            self.config_status_label.setText(
                f"BattlEye config: not found "
                f"(panel RConPort {port})"
            )

        self._append_system(
            f"RCon target: {host}:{port}"
        )

        if config_exists and config_path:
            if config_active:
                self._append_system(
                    f"Found active BattlEye config: "
                    f"{config_path}"
                )

            else:
                self._append_system(
                    f"Found BattlEye config: "
                    f"{config_path}"
                )

            if (
                remote_port is not None
                and int(remote_port) != port
            ):
                self._append_system(
                    f"WARNING: remote config says RConPort "
                    f"{remote_port}, but the panel is using "
                    f"manually selected port {port}."
                )

        else:
            self._append_system(
                "No BattlEye RCon config was found through SSH."
            )

            self._append_system(
                f"Using panel RCon port {port}."
            )

        client = BattlEyeRConClient(
            host,
            port,
            password,
            on_message=self._rcon_message_callback,
            on_disconnect=self._rcon_disconnect_callback,
            on_error=self._rcon_error_callback,
        )

        self.rcon = client

        self._append_system(
            f"Connecting to {host}:{port}..."
        )

        self.jobs.start(
            client.connect,
            on_ok=self._on_connected,
            on_fail=self._on_connect_failed,
        )

    def _on_connected(self, _result):
        self._connecting = False

        self._update_connection_ui(
            True
        )

        self._append_system(
            "BattlEye RCon connected."
        )

    def _on_connect_failed(self, error):
        self._connecting = False

        self._update_connection_ui(
            False
        )

        if isinstance(
            error,
            BattlEyeRConAuthenticationError,
        ):
            message = (
                "BattlEye RCon authentication failed. "
                "Check RConPassword in the active BattlEye "
                "configuration."
            )

        elif isinstance(
            error,
            BattlEyeRConTimeout,
        ):
            message = (
                "BattlEye RCon connection timed out. "
                "Check that the panel port matches RConPort "
                "in the active BattlEye config, that UDP RCon "
                "traffic is allowed by the server/hosting firewall, "
                "and that BattlEye RCon is listening."
            )

        else:
            message = str(
                error
            )

        self._append_error(
            message
        )

        if self.rcon is not None:
            self.rcon.disconnect(
                notify=False
            )

            self.rcon = None

    def disconnect(self):
        self._manual_disconnect = True
        self._connecting = False

        client = self.rcon

        self.rcon = None

        if client is not None:
            self.jobs.start(
                lambda: client.disconnect(
                    notify=False
                ),
                on_ok=lambda _result: (
                    self._on_disconnected()
                ),
                on_fail=lambda error: (
                    self._on_disconnect_failed(
                        error
                    )
                ),
            )

        else:
            self._on_disconnected()

    def _on_disconnected(self):
        self._update_connection_ui(
            False
        )

        self._append_system(
            "BattlEye RCon disconnected."
        )

    def _on_disconnect_failed(self, error):
        self._update_connection_ui(
            False
        )

        self._append_error(
            f"RCon disconnect error: {error}"
        )

    # ==============================================================
    # RCON CALLBACKS
    # ==============================================================

    def _rcon_message_callback(self, message):
        self.rcon_message_signal.emit(
            str(message)
        )

    def _rcon_disconnect_callback(self):
        self.rcon_disconnect_signal.emit()

    def _rcon_error_callback(self, message):
        self.rcon_error_signal.emit(
            str(message)
        )

    def _handle_connection_lost(self):
        if self._manual_disconnect:
            self._update_connection_ui(
                False
            )

            return

        self._update_connection_ui(
            False
        )

        self._append_error(
            "BattlEye RCon connection lost."
        )

        self.rcon = None

    # ==============================================================
    # COMMANDS
    # ==============================================================

    def send_current_command(self):
        command = (
            self.command_edit.text().strip()
        )

        if not command:
            return

        self.command_edit.add_history(
            command
        )

        self.command_edit.clear()

        self._send_command(
            command
        )

    def _send_command(self, command):
        command = command.strip()

        if not command:
            return

        client = self.rcon

        if (
            client is None
            or not client.connected
        ):
            self._append_error(
                "RCon is not connected."
            )
            return

        self._append_command(
            command
        )

        self.jobs.start(
            lambda: client.send_command(
                command
            ),
            on_fail=self._on_command_failed,
        )

    def _on_command_failed(self, error):
        self._append_error(
            f"Command failed: {error}"
        )

    # ==============================================================
    # QUICK COMMANDS
    # ==============================================================

    def _quick_say(self):
        text, ok = QInputDialog.getText(
            self,
            "BattlEye #say",
            "Message:",
        )

        if not ok or not text.strip():
            return

        self._send_command(
            f"say -1 {text.strip()}"
        )

    def _quick_kick(self):
        player, ok = QInputDialog.getText(
            self,
            "BattlEye #kick",
            "Player number/name:",
        )

        if not ok or not player.strip():
            return

        reason, ok = QInputDialog.getText(
            self,
            "BattlEye #kick",
            "Reason (optional):",
        )

        if not ok:
            return

        command = (
            f"kick {player.strip()}"
        )

        if reason.strip():
            command += (
                f" {reason.strip()}"
            )

        self._send_command(
            command
        )

    def _quick_ban(self):
        player, ok = QInputDialog.getText(
            self,
            "BattlEye #ban",
            "Player number/name:",
        )

        if not ok or not player.strip():
            return

        duration, ok = QInputDialog.getInt(
            self,
            "BattlEye #ban",
            "Duration in minutes (0 = permanent):",
            0,
            0,
            525600,
        )

        if not ok:
            return

        reason, ok = QInputDialog.getText(
            self,
            "BattlEye #ban",
            "Reason (optional):",
        )

        if not ok:
            return

        command = (
            f"ban {player.strip()}"
        )

        if duration > 0:
            command += (
                f" {duration}"
            )

        if reason.strip():
            command += (
                f" {reason.strip()}"
            )

        self._send_command(
            command
        )

    def _quick_shutdown(self):
        answer = QMessageBox.warning(
            self,
            "Shutdown DayZ server",
            "Send the BattlEye shutdown command?",
            QMessageBox.Yes
            | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self._send_command(
            "shutdown"
        )

    # ==============================================================
    # AUTO RECONNECT
    # ==============================================================

    def _auto_reconnect_tick(self):
        if not self.auto_reconnect_checkbox.isChecked():
            return

        if self._manual_disconnect:
            return

        if self.connected:
            return

        if self._connecting:
            return

        host = (
            self.host_edit.text().strip()
        )

        password = (
            self.password_edit.text()
        )

        if not host or not password:
            return

        self.connect()

    # ==============================================================
    # UI STATE
    # ==============================================================

    def _update_connection_ui(
        self,
        connected,
        connecting=False,
    ):
        if connecting:
            self._set_status_indicator(
                "connecting"
            )

            self.connect_button.setText(
                "Connecting..."
            )

        elif connected:
            self._set_status_indicator(
                "connected"
            )

            self.connect_button.setText(
                "Disconnect"
            )

        else:
            self._set_status_indicator(
                "disconnected"
            )

            self.connect_button.setText(
                "Connect"
            )

        self.host_edit.setEnabled(
            not connected
            and not connecting
        )

        self.password_edit.setEnabled(
            not connected
            and not connecting
        )

        # Port is editable while disconnected,
        # exactly like the Host field.
        self.port_edit.setEnabled(
            not connected
            and not connecting
        )

        self.setup_button.setEnabled(
            not connecting
        )

        self.send_button.setEnabled(
            connected
        )

        self.command_edit.setEnabled(
            connected
        )

    # ==============================================================
    # CONSOLE
    # ==============================================================

    @staticmethod
    def _timestamp():
        return datetime.now().strftime(
            "%H:%M:%S"
        )

    def _append(self, prefix, text):
        self.console.appendPlainText(
            f"[{self._timestamp()}] "
            f"{prefix}{text}"
        )

        scrollbar = (
            self.console.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )

    def _append_system(self, text):
        self._append(
            "",
            text,
        )

    def _append_command(self, text):
        self._append(
            "> ",
            text,
        )

    def _append_response(self, text):
        self._append(
            "< ",
            text,
        )

    def _append_error(self, text):
        self._append(
            "ERROR: ",
            text,
        )

    # ==============================================================
    # SHUTDOWN
    # ==============================================================

    def close(self):
        """
        Stop RCon before WorkerRegistry is shut down.
        """

        self._manual_disconnect = True

        self._reconnect_timer.stop()

        client = self.rcon

        self.rcon = None

        if client is not None:
            client.disconnect(
                notify=False
            )

        self._update_connection_ui(
            False
        )
