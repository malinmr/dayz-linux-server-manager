import re
import shlex

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
)

from worker import WorkerRegistry


APPID_STABLE = "223350"
APPID_EXPERIMENTAL = "1042420"

STEAMCMD_DOWNLOAD_URL = (
    "https://steamcdn-a.akamaihd.net/client/installer/"
    "steamcmd_linux.tar.gz"
)


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

        self._build_ui()

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
            "one for your setup."
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

            return {
                "steamcmd": detected,
                "stable": stable,
                "experimental": experimental,
                "systemd": systemd,
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

        self.detected_steamcmd = detected

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

        login_part = (
            f"+login {shlex.quote(steam_user)}"
        )

        if steam_password:
            login_part += (
                f" {shlex.quote(steam_password)}"
            )

        server_root = server_root.rstrip("/")

        steamcmd = shlex.quote(
            detected
        )

        quoted_root = shlex.quote(
            server_root
        )

        cmd = (
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
            result = self.ssh.exec(
                cmd,
                timeout=3600,
            )

            code, output, error = result

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
                "steamcmd_output": output or "",
                "steamcmd_error": error or "",
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
