import re
import shlex

from PySide6.QtCore import QSignalBlocker, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from worker import WorkerRegistry


class SystemdPanel(QWidget):
    """
    Manages the DayZ systemd service unit and its launch parameters.

    The -profiles= parameter is special because it determines where
    DayZ writes profiles/log-related files.

    Rules:

        -profiles=profiles
            -> <server_root>/profiles

        -profiles=/some/absolute/path
            -> /some/absolute/path

        -profiles=some/relative/path
            -> <server_root>/some/relative/path

    The resolved path is exposed through profiles_path_changed so that
    MainWindow can keep FilesPanel synchronized without the panels
    directly knowing about each other.
    """

    profiles_path_changed = Signal(str)

    RESTART_VALUES = [
        "no",
        "on-success",
        "on-failure",
        "on-abnormal",
        "on-watchdog",
        "on-abort",
        "always",
    ]

    def __init__(
        self,
        ssh,
        config,
        sudo_password_getter=None,
        parent=None,
    ):
        super().__init__(parent)

        self.ssh = ssh
        self.config = config
        self.sudo_password_getter = sudo_password_getter

        self.jobs = WorkerRegistry()

        self._build_ui()

        self.set_connected(False)

        # --------------------------------------------------------------
        # First-start/default profiles parameter.
        # --------------------------------------------------------------

        profiles_value = (
            self.config.profiles_arg.strip()
            or "profiles"
        )

        blocker = QSignalBlocker(
            self.profiles_param
        )

        self.profiles_param.setText(
            profiles_value
        )

        del blocker

        self._update_profiles_path(
            emit_signal=False,
            save_config=False,
        )

        # --------------------------------------------------------------
        # Default service settings.
        # --------------------------------------------------------------

        self.user_param.setText(
            self.config.username.strip()
        )

        self.group_param.setText(
            "users"
        )

        self.runtime_max_sec_param.setText(
            "14520s"
        )

        self.restart_param.setCurrentText(
            "always"
        )

        self.restart_sec_param.setText(
            "5s"
        )

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        root.setContentsMargins(
            8,
            8,
            8,
            8,
        )

        root.setSpacing(6)

        # --------------------------------------------------------------
        # Header
        # --------------------------------------------------------------

        header = QHBoxLayout()
        header.setSpacing(6)

        title = QLabel(
            "Systemd Service"
        )

        title.setStyleSheet(
            "font-size: 18px; font-weight: bold;"
        )

        self.connection_label = QLabel(
            "Disconnected"
        )

        self.load_button = QPushButton(
            "Load"
        )

        self.load_button.clicked.connect(
            self.load_unit
        )

        self.save_button = QPushButton(
            "Save"
        )

        self.save_button.clicked.connect(
            self.save_unit
        )

        header.addWidget(
            title
        )

        header.addStretch()

        header.addWidget(
            self.connection_label
        )

        header.addWidget(
            self.load_button
        )

        header.addWidget(
            self.save_button
        )

        root.addLayout(
            header
        )

        # --------------------------------------------------------------
        # FIXED TOP:
        # Raw systemd editor
        #
        # This intentionally sits OUTSIDE the scroll area.
        # --------------------------------------------------------------

        editor_group = QGroupBox(
            "Systemd Unit — manually editable"
        )

        editor_layout = QVBoxLayout(
            editor_group
        )

        editor_layout.setContentsMargins(
            8,
            6,
            8,
            6,
        )

        editor_note = QLabel(
            "The unit below is the actual systemd service definition. "
            "You can edit it directly or use the controls below."
        )

        editor_note.setWordWrap(
            True
        )

        self.editor = QPlainTextEdit()

        self.editor.setPlaceholderText(
            "[Unit]\n"
            "Description=DayZ Dedicated Server\n\n"
            "[Service]\n"
            "ExecStart=/path/to/DayZServer ...\n"
        )

        self.editor.setMinimumHeight(
            180
        )

        self.editor.setMaximumHeight(
            260
        )

        editor_layout.addWidget(
            editor_note
        )

        editor_layout.addWidget(
            self.editor
        )

        root.addWidget(
            editor_group
        )

        # --------------------------------------------------------------
        # SCROLLABLE CONTENT
        #
        # ONLY this middle section scrolls.
        # --------------------------------------------------------------

        scroll = QScrollArea()

        scroll.setWidgetResizable(
            True
        )

        scroll.setFrameShape(
            QFrame.NoFrame
        )

        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        content = QWidget()

        content_layout = QVBoxLayout(
            content
        )

        content_layout.setContentsMargins(
            2,
            2,
            8,
            2,
        )

        content_layout.setSpacing(
            6
        )

        scroll.setWidget(
            content
        )

        root.addWidget(
            scroll,
            1,
        )

        # --------------------------------------------------------------
        # Service
        # --------------------------------------------------------------

        service_group = QGroupBox(
            "Service"
        )

        service_layout = QHBoxLayout(
            service_group
        )

        service_layout.setContentsMargins(
            8,
            6,
            8,
            6,
        )

        service_layout.setSpacing(
            8
        )

        service_layout.addWidget(
            QLabel("Service Name:")
        )

        self.service_edit = QLineEdit(
            self.config.systemd_service
        )

        self.service_edit.setMinimumWidth(
            180
        )

        self.service_edit.setMaximumWidth(
            280
        )

        service_layout.addWidget(
            self.service_edit
        )

        service_layout.addStretch()

        content_layout.addWidget(
            service_group
        )

        # --------------------------------------------------------------
        # Service options
        # --------------------------------------------------------------

        runtime_group = QGroupBox(
            "Service Options"
        )

        runtime_layout = QFormLayout(
            runtime_group
        )

        runtime_layout.setContentsMargins(
            8,
            6,
            8,
            6,
        )

        runtime_layout.setHorizontalSpacing(
            12
        )

        runtime_layout.setVerticalSpacing(
            4
        )

        self.user_param = QLineEdit()

        self.user_param.setPlaceholderText(
            "Linux user"
        )

        self.group_param = QLineEdit()

        self.group_param.setPlaceholderText(
            "Linux group"
        )

        self.runtime_max_sec_param = QLineEdit()

        self.runtime_max_sec_param.setPlaceholderText(
            "e.g. 14520s, 4h, 30min"
        )

        self.restart_param = QComboBox()

        self.restart_param.setEditable(
            True
        )

        self.restart_param.addItems(
            self.RESTART_VALUES
        )

        self.restart_sec_param = QLineEdit()

        self.restart_sec_param.setPlaceholderText(
            "e.g. 5s, 1min"
        )

        runtime_layout.addRow(
            "User:",
            self.user_param,
        )

        runtime_layout.addRow(
            "Group:",
            self.group_param,
        )

        runtime_layout.addRow(
            "RuntimeMaxSec:",
            self.runtime_max_sec_param,
        )

        runtime_layout.addRow(
            "Restart:",
            self.restart_param,
        )

        runtime_layout.addRow(
            "RestartSec:",
            self.restart_sec_param,
        )

        service_buttons = QHBoxLayout()

        service_buttons.setSpacing(
            6
        )

        self.apply_service_settings_button = QPushButton(
            "Apply to Unit"
        )

        self.apply_service_settings_button.clicked.connect(
            self.apply_service_settings_to_editor
        )

        self.parse_service_settings_button = QPushButton(
            "Reload from Unit"
        )

        self.parse_service_settings_button.clicked.connect(
            self.parse_service_settings_from_editor
        )

        service_buttons.addWidget(
            self.apply_service_settings_button
        )

        service_buttons.addWidget(
            self.parse_service_settings_button
        )

        service_buttons.addStretch()

        runtime_layout.addRow(
            "",
            service_buttons,
        )

        content_layout.addWidget(
            runtime_group
        )

        # --------------------------------------------------------------
        # DayZ launch parameters
        # --------------------------------------------------------------

        parameters_group = QGroupBox(
            "DayZ Launch Parameters"
        )

        parameters_layout = QVBoxLayout(
            parameters_group
        )

        parameters_layout.setContentsMargins(
            8,
            6,
            8,
            6,
        )

        parameters_layout.setSpacing(
            5
        )

        parameters_note = QLabel(
            "These fields control the DayZ ExecStart command. "
            "Apply them to the unit before saving."
        )

        parameters_note.setWordWrap(
            True
        )

        parameters_layout.addWidget(
            parameters_note
        )

        # --------------------------------------------------------------
        # Basic parameters
        # --------------------------------------------------------------

        basic_layout = QFormLayout()

        basic_layout.setHorizontalSpacing(
            12
        )

        basic_layout.setVerticalSpacing(
            4
        )

        self.config_param = QLineEdit()

        self.config_param.setPlaceholderText(
            "e.g. serverDZ.cfg"
        )

        self.port_param = QSpinBox()

        self.port_param.setRange(
            0,
            65535,
        )

        self.port_param.setValue(
            0
        )

        self.port_param.setSpecialValueText(
            "Not set"
        )

        self.profiles_param = QLineEdit(
            "profiles"
        )

        self.profiles_param.setPlaceholderText(
            "profiles or absolute path"
        )

        self.profiles_param.textChanged.connect(
            self._profiles_parameter_changed
        )

        self.bepath_param = QLineEdit()

        self.bepath_param.setPlaceholderText(
            "absolute path to battleye folder"
        )

        self.cpu_count_param = QSpinBox()

        self.cpu_count_param.setRange(
            0,
            256,
        )

        self.cpu_count_param.setValue(
            0
        )

        self.cpu_count_param.setSpecialValueText(
            "Not set"
        )

        self.limit_fps_param = QSpinBox()

        self.limit_fps_param.setRange(
            0,
            1000,
        )

        self.limit_fps_param.setValue(
            0
        )

        self.limit_fps_param.setSpecialValueText(
            "Not set"
        )

        basic_layout.addRow(
            "-config=",
            self.config_param,
        )

        basic_layout.addRow(
            "-port=",
            self.port_param,
        )

        basic_layout.addRow(
            "-profiles=",
            self.profiles_param,
        )

        basic_layout.addRow(
            "-BEpath=",
            self.bepath_param,
        )

        basic_layout.addRow(
            "-cpuCount=",
            self.cpu_count_param,
        )

        basic_layout.addRow(
            "-limitFPS=",
            self.limit_fps_param,
        )

        parameters_layout.addLayout(
            basic_layout
        )

        # --------------------------------------------------------------
        # Boolean parameters
        # --------------------------------------------------------------

        flags_group = QGroupBox(
            "Flags"
        )

        flags_layout = QHBoxLayout(
            flags_group
        )

        flags_layout.setContentsMargins(
            8,
            4,
            8,
            4,
        )

        self.freezecheck_param = QCheckBox(
            "-freezecheck"
        )

        self.dologs_param = QCheckBox(
            "-dologs"
        )

        self.adminlog_param = QCheckBox(
            "-adminlog"
        )

        self.netlog_param = QCheckBox(
            "-netlog"
        )

        self.file_patching_param = QCheckBox(
            "-filePatching"
        )

        flags_layout.addWidget(
            self.freezecheck_param
        )

        flags_layout.addWidget(
            self.dologs_param
        )

        flags_layout.addWidget(
            self.adminlog_param
        )

        flags_layout.addWidget(
            self.netlog_param
        )

        flags_layout.addWidget(
            self.file_patching_param
        )

        flags_layout.addStretch()

        parameters_layout.addWidget(
            flags_group
        )

        # --------------------------------------------------------------
        # Mods
        # --------------------------------------------------------------

        mods_group = QGroupBox(
            "Mods"
        )

        mods_layout = QFormLayout(
            mods_group
        )

        mods_layout.setContentsMargins(
            8,
            4,
            8,
            4,
        )

        self.mod_param = QPlainTextEdit()

        self.mod_param.setPlaceholderText(
            "1559212036;1564026768;..."
        )

        self.mod_param.setFixedHeight(
            52
        )

        self.servermod_param = QPlainTextEdit()

        self.servermod_param.setPlaceholderText(
            "3740866089;..."
        )

        self.servermod_param.setFixedHeight(
            52
        )

        mods_layout.addRow(
            "-mod=",
            self.mod_param,
        )

        mods_layout.addRow(
            "-servermod=",
            self.servermod_param,
        )

        parameters_layout.addWidget(
            mods_group
        )

        # --------------------------------------------------------------
        # Custom parameters
        # --------------------------------------------------------------

        custom_group = QGroupBox(
            "Custom / Advanced Parameters"
        )

        custom_layout = QVBoxLayout(
            custom_group
        )

        custom_layout.setContentsMargins(
            8,
            4,
            8,
            4,
        )

        self.custom_param = QPlainTextEdit()

        self.custom_param.setPlaceholderText(
            "Additional DayZ launch parameters, one per line."
        )

        self.custom_param.setFixedHeight(
            55
        )

        custom_layout.addWidget(
            self.custom_param
        )

        parameters_layout.addWidget(
            custom_group
        )

        # --------------------------------------------------------------
        # Parameter buttons
        # --------------------------------------------------------------

        parameter_buttons = QHBoxLayout()

        parameter_buttons.setSpacing(
            6
        )

        self.apply_parameters_button = QPushButton(
            "Apply Parameters to ExecStart"
        )

        self.apply_parameters_button.clicked.connect(
            self.apply_parameters_to_editor
        )

        self.parse_parameters_button = QPushButton(
            "Reload from ExecStart"
        )

        self.parse_parameters_button.clicked.connect(
            self.parse_parameters_from_editor
        )

        parameter_buttons.addWidget(
            self.apply_parameters_button
        )

        parameter_buttons.addWidget(
            self.parse_parameters_button
        )

        parameter_buttons.addStretch()

        parameters_layout.addLayout(
            parameter_buttons
        )

        content_layout.addWidget(
            parameters_group
        )

        # --------------------------------------------------------------
        # Scheduled restart
        # --------------------------------------------------------------

        timer_group = QGroupBox(
            "Scheduled Restart"
        )

        timer_layout = QHBoxLayout(
            timer_group
        )

        timer_layout.setContentsMargins(
            8,
            5,
            8,
            5,
        )

        timer_layout.setSpacing(
            6
        )

        timer_layout.addWidget(
            QLabel("Daily Restart:")
        )

        self.timer_time_edit = QLineEdit()

        self.timer_time_edit.setPlaceholderText(
            "HH:MM"
        )

        self.timer_time_edit.setMaximumWidth(
            100
        )

        self.timer_apply_button = QPushButton(
            "Apply Timer"
        )

        self.timer_apply_button.clicked.connect(
            self.apply_restart_timer
        )

        self.timer_remove_button = QPushButton(
            "Remove Timer"
        )

        self.timer_remove_button.clicked.connect(
            self.remove_restart_timer
        )

        timer_layout.addWidget(
            self.timer_time_edit
        )

        timer_layout.addWidget(
            self.timer_apply_button
        )

        timer_layout.addWidget(
            self.timer_remove_button
        )

        timer_layout.addStretch()

        content_layout.addWidget(
            timer_group
        )

        content_layout.addStretch()

        # --------------------------------------------------------------
        # FIXED BOTTOM:
        # Output
        #
        # This intentionally sits OUTSIDE the scroll area.
        # --------------------------------------------------------------

        output_group = QGroupBox(
            "Output"
        )

        output_layout = QVBoxLayout(
            output_group
        )

        output_layout.setContentsMargins(
            8,
            5,
            8,
            5,
        )

        self.output = QPlainTextEdit()

        self.output.setReadOnly(
            True
        )

        self.output.setMaximumBlockCount(
            1000
        )

        self.output.setFixedHeight(
            90
        )

        output_layout.addWidget(
            self.output
        )

        root.addWidget(
            output_group
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _service_name(self):
        service = (
            self.service_edit.text().strip()
        )

        if not service:
            service = (
                self.config.systemd_service.strip()
            )

        if service.endswith(
            ".service"
        ):
            service = service[:-8]

        return service

    def _unit_path(self):
        return (
            f"/etc/systemd/system/"
            f"{self._service_name()}.service"
        )

    def _sudo_password(self):
        if self.sudo_password_getter:
            return (
                self.sudo_password_getter()
                or ""
            )

        return ""

    def _set_controls_enabled(
        self,
        enabled,
    ):
        self.load_button.setEnabled(
            enabled
        )

        self.save_button.setEnabled(
            enabled
        )

        self.apply_service_settings_button.setEnabled(
            enabled
        )

        self.parse_service_settings_button.setEnabled(
            enabled
        )

        self.apply_parameters_button.setEnabled(
            enabled
        )

        self.parse_parameters_button.setEnabled(
            enabled
        )

        self.timer_apply_button.setEnabled(
            enabled
        )

        self.timer_remove_button.setEnabled(
            enabled
        )

    def set_connected(
        self,
        connected,
    ):
        if connected:
            self.connection_label.setText(
                "Connected"
            )

            self._set_controls_enabled(
                True
            )

        else:
            self.connection_label.setText(
                "Disconnected"
            )

            self._set_controls_enabled(
                False
            )

    def _append_output(
        self,
        text,
    ):
        if text:
            self.output.appendPlainText(
                text.rstrip()
            )

    # ------------------------------------------------------------------
    # Service settings
    # ------------------------------------------------------------------

    def _service_setting_values(self):
        return {
            "User": self.user_param.text().strip(),
            "Group": self.group_param.text().strip(),
            "RuntimeMaxSec": (
                self.runtime_max_sec_param.text().strip()
            ),
            "Restart": (
                self.restart_param.currentText().strip()
            ),
            "RestartSec": (
                self.restart_sec_param.text().strip()
            ),
        }

    def _find_service_section(
        self,
        lines,
    ):
        service_start = None
        service_end = len(lines)

        for index, line in enumerate(lines):
            stripped = line.strip()

            if stripped == "[Service]":
                service_start = index
                break

        if service_start is None:
            return None, None

        for index in range(
            service_start + 1,
            len(lines),
        ):
            stripped = lines[index].strip()

            if (
                stripped.startswith("[")
                and stripped.endswith("]")
            ):
                service_end = index
                break

        return (
            service_start,
            service_end,
        )

    def _replace_service_setting(
        self,
        lines,
        key,
        value,
    ):
        """
        Replace an existing directive inside [Service].

        If the directive does not exist, add it at the end of the
        [Service] section.

        Duplicate occurrences are removed.
        """

        service_start, service_end = (
            self._find_service_section(lines)
        )

        if service_start is None:
            return lines

        pattern = re.compile(
            rf"^\s*{re.escape(key)}\s*="
        )

        result = []
        inserted = False

        for index, line in enumerate(lines):
            if (
                service_start < index < service_end
                and pattern.match(line)
            ):
                if not inserted:
                    result.append(
                        f"{key}={value}"
                    )

                    inserted = True

                continue

            result.append(
                line
            )

        if not inserted:
            result.insert(
                service_end,
                f"{key}={value}",
            )

        return result

    def _remove_service_setting(
        self,
        lines,
        key,
    ):
        service_start, service_end = (
            self._find_service_section(lines)
        )

        if service_start is None:
            return lines

        pattern = re.compile(
            rf"^\s*{re.escape(key)}\s*="
        )

        return [
            line
            for index, line in enumerate(lines)
            if not (
                service_start < index < service_end
                and pattern.match(line)
            )
        ]

    def _service_setting_from_editor(
        self,
        key,
    ):
        lines = (
            self.editor.toPlainText()
            .splitlines()
        )

        in_service = False

        pattern = re.compile(
            rf"^\s*{re.escape(key)}\s*=\s*(.*)$"
        )

        for line in lines:
            stripped = line.strip()

            if stripped == "[Service]":
                in_service = True
                continue

            if (
                in_service
                and stripped.startswith("[")
                and stripped.endswith("]")
            ):
                break

            if in_service:
                match = pattern.match(line)

                if match:
                    return match.group(1).strip()

        return ""

    def parse_service_settings_from_editor(
        self,
    ):
        text = self.editor.toPlainText()

        if not text.strip():
            self._append_output(
                "Systemd unit editor is empty."
            )
            return

        values = {
            key: self._service_setting_from_editor(
                key
            )
            for key in (
                "User",
                "Group",
                "RuntimeMaxSec",
                "Restart",
                "RestartSec",
            )
        }

        self.user_param.setText(
            values["User"]
        )

        self.group_param.setText(
            values["Group"]
        )

        self.runtime_max_sec_param.setText(
            values["RuntimeMaxSec"]
        )

        restart_value = (
            values["Restart"]
            or "always"
        )

        if (
            restart_value
            not in self.RESTART_VALUES
        ):
            if (
                self.restart_param.findText(
                    restart_value
                )
                == -1
            ):
                self.restart_param.addItem(
                    restart_value
                )

        self.restart_param.setCurrentText(
            restart_value
        )

        self.restart_sec_param.setText(
            values["RestartSec"]
        )

        self._append_output(
            "Service settings loaded from systemd unit."
        )

    def apply_service_settings_to_editor(
        self,
    ):
        text = self.editor.toPlainText()

        if not text.strip():
            QMessageBox.warning(
                self,
                "Empty Systemd Unit",
                "Load or enter a systemd unit first.",
            )
            return

        if "[Service]" not in text:
            QMessageBox.warning(
                self,
                "Service Section Not Found",
                "The systemd unit does not contain a [Service] section.",
            )
            return

        values = self._service_setting_values()

        if not values["User"]:
            QMessageBox.warning(
                self,
                "User Required",
                "Enter the Linux user that should run the DayZ server.",
            )
            return

        if not values["Restart"]:
            QMessageBox.warning(
                self,
                "Restart Policy Required",
                "Select a systemd Restart policy.",
            )
            return

        lines = text.splitlines()

        for key, value in values.items():
            if value:
                lines = self._replace_service_setting(
                    lines,
                    key,
                    value,
                )
            else:
                lines = self._remove_service_setting(
                    lines,
                    key,
                )

        self.editor.setPlainText(
            "\n".join(lines)
        )

        self._append_output(
            "Service settings applied to the systemd unit. "
            "Review the unit above before saving."
        )

    # ------------------------------------------------------------------
    # Profiles path handling
    # ------------------------------------------------------------------

    def _resolve_profiles_path(
        self,
        profiles_value=None,
    ):
        """
        Resolve the DayZ -profiles= parameter.

        Absolute values are used directly.

        Relative values are resolved against the authoritative
        DayZ server root.
        """

        if profiles_value is None:
            profiles_value = (
                self.profiles_param.text().strip()
            )

        profiles_value = (
            profiles_value.strip()
        )

        if not profiles_value:
            profiles_value = "profiles"

        if profiles_value.startswith("/"):
            return (
                profiles_value.rstrip("/")
                or "/"
            )

        server_root = (
            self.config.server_root.strip()
        )

        if not server_root:
            return (
                profiles_value.rstrip("/")
            )

        server_root = (
            server_root.rstrip("/")
        )

        return (
            f"{server_root}/"
            f"{profiles_value.lstrip('/')}"
        ).rstrip("/")

    def _update_profiles_path(
        self,
        emit_signal=True,
        save_config=False,
        profiles_value=None,
    ):
        """
        Synchronize the -profiles= value with application configuration.

        profiles_arg stores the actual value used by DayZ.

        profiles_dir stores the resolved filesystem path used by the
        Files panel.

        log_dir follows the resolved profiles directory.
        """

        if profiles_value is None:
            profiles_value = (
                self.profiles_param.text().strip()
                or "profiles"
            )
        else:
            profiles_value = (
                profiles_value.strip()
                or "profiles"
            )

        self.config.profiles_arg = (
            profiles_value
        )

        resolved_path = (
            self._resolve_profiles_path(
                profiles_value
            )
        )

        self.config.profiles_dir = (
            resolved_path
        )

        self.config.log_dir = (
            resolved_path
        )

        if save_config:
            self.config.save()

        if emit_signal:
            self.profiles_path_changed.emit(
                resolved_path
            )

        return resolved_path

    def _profiles_parameter_changed(
        self,
        value,
    ):
        """
        Keep the application's profiles configuration synchronized
        with the current -profiles= field.

        This does not modify the remote systemd unit and does not
        persist configuration to disk.
        """

        self._update_profiles_path(
            emit_signal=True,
            save_config=False,
            profiles_value=value,
        )

    # ------------------------------------------------------------------
    # Receive mod parameters from Workshop Mods
    # ------------------------------------------------------------------

    def set_mod_parameters(
        self,
        normal_value,
        server_value,
    ):
        """
        Receive generated -mod= and -servermod= values from the
        Workshop Mods panel.

        This only changes the parameter fields in this panel.

        It does NOT:
            - modify the big systemd editor
            - save anything remotely
            - run systemd-analyze
            - daemon-reload systemd

        The user must still review the values and click
        "Apply Parameters to ExecStart".
        """

        self.mod_param.setPlainText(
            normal_value or ""
        )

        self.servermod_param.setPlainText(
            server_value or ""
        )

        self._append_output(
            "Mod parameters received from Workshop Mods."
        )

        self._append_output(
            f"-mod={normal_value}"
        )

        self._append_output(
            f"-servermod={server_value}"
        )

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load_unit(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the server first.",
            )
            return

        path = self._unit_path()

        self._append_output(
            f"Loading systemd unit: {path}"
        )

        def work():
            return self.ssh.read_file(
                path
            )

        def ok(content):
            self.editor.setPlainText(
                content
            )

            self.parse_service_settings_from_editor()
            self.parse_parameters_from_editor()

            self._append_output(
                "Systemd unit loaded successfully."
            )

        def fail(error):
            self._append_output(
                f"Load failed: {error}"
            )

            QMessageBox.critical(
                self,
                "Load Failed",
                error,
            )

        self.jobs.start(
            work,
            on_ok=ok,
            on_fail=fail,
        )

    def save_unit(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the server first.",
            )
            return

        service_name = (
            self._service_name()
        )

        if not service_name:
            QMessageBox.warning(
                self,
                "Invalid Service Name",
                "Enter a systemd service name first.",
            )
            return

        path = (
            f"/etc/systemd/system/"
            f"{service_name}.service"
        )

        content = self.editor.toPlainText()

        if not content.strip():
            QMessageBox.warning(
                self,
                "Empty Unit",
                "The systemd unit editor is empty.",
            )
            return

        password = self._sudo_password()

        if not password:
            QMessageBox.warning(
                self,
                "Sudo Password Required",
                "Enter the sudo password on the Server Status tab first.",
            )
            return

        profiles_value = (
            self.profiles_param.text().strip()
            or "profiles"
        )

        resolved_profiles_path = (
            self._update_profiles_path(
                emit_signal=True,
                save_config=False,
                profiles_value=profiles_value,
            )
        )

        self._append_output(
            f"Saving systemd unit: {path}"
        )

        def work():
            temp_path = (
                f"/tmp/{service_name}.service"
            )

            self.ssh.write_file(
                temp_path,
                content,
                backup=False,
            )

            verify_command = (
                "systemd-analyze verify "
                + shlex.quote(temp_path)
            )

            code, out, err = self.ssh.exec_sudo(
                verify_command,
                password,
            )

            if code != 0:
                try:
                    self.ssh.exec(
                        "rm -f "
                        + shlex.quote(temp_path)
                    )
                except Exception:
                    pass

                raise RuntimeError(
                    "systemd-analyze verify failed:\n"
                    + (
                        err
                        or out
                        or "Unknown validation error"
                    )
                )

            install_command = (
                "cp -f "
                + shlex.quote(temp_path)
                + " "
                + shlex.quote(path)
                + " && "
                + "rm -f "
                + shlex.quote(temp_path)
                + " && "
                + "systemctl daemon-reload"
            )

            code, out, err = self.ssh.exec_sudo(
                "sh -c "
                + shlex.quote(install_command),
                password,
            )

            if code != 0:
                raise RuntimeError(
                    err
                    or out
                    or "Failed to install systemd unit."
                )

            return out

        def ok(output):
            self.config.systemd_service = (
                service_name
            )

            self.config.profiles_arg = (
                profiles_value
            )

            self.config.profiles_dir = (
                resolved_profiles_path
            )

            self.config.log_dir = (
                resolved_profiles_path
            )

            self.config.save()

            self._append_output(
                "Systemd unit saved and daemon-reloaded."
            )

            if output:
                self._append_output(
                    output
                )

        def fail(error):
            self._append_output(
                f"Save failed: {error}"
            )

            QMessageBox.critical(
                self,
                "Save Failed",
                error,
            )

        self.jobs.start(
            work,
            on_ok=ok,
            on_fail=fail,
        )

    # ------------------------------------------------------------------
    # ExecStart parsing
    # ------------------------------------------------------------------

    def _get_execstart(
        self,
        text,
    ):
        lines = text.splitlines()

        exec_lines = []

        collecting = False

        for line in lines:
            stripped = line.strip()

            if not collecting:
                if stripped.startswith(
                    "ExecStart="
                ):
                    collecting = True

                    exec_lines.append(
                        stripped[
                            len("ExecStart="):
                        ]
                    )

                    if not stripped.endswith(
                        "\\"
                    ):
                        break

            else:
                exec_lines.append(
                    stripped
                )

                if not stripped.endswith(
                    "\\"
                ):
                    break

        if not exec_lines:
            return ""

        command = " ".join(
            part[:-1].rstrip()
            if part.endswith("\\")
            else part
            for part in exec_lines
        )

        return command.strip()

    def _parse_execstart_tokens(
        self,
        command,
    ):
        if not command:
            return []

        try:
            return shlex.split(
                command,
                posix=True,
            )
        except ValueError:
            return command.split()

    def _set_spinbox_optional(
        self,
        widget,
        value,
    ):
        if value is None or value == "":
            widget.setValue(0)
            return

        try:
            widget.setValue(
                int(value)
            )
        except (TypeError, ValueError):
            widget.setValue(0)

    def _clean_parameter_value(
        self,
        value,
    ):
        return (
            value
            .strip()
            .strip('"')
            .strip("'")
        )

    def parse_parameters_from_editor(
        self,
    ):
        text = self.editor.toPlainText()

        command = self._get_execstart(
            text
        )

        if not command:
            self._append_output(
                "No ExecStart= line found."
            )
            return

        tokens = self._parse_execstart_tokens(
            command
        )

        if not tokens:
            return

        args = tokens[1:]

        known_keys = {
            "config",
            "port",
            "profiles",
            "BEpath",
            "cpuCount",
            "limitFPS",
            "mod",
            "servermod",
        }

        known_flags = {
            "freezecheck",
            "dologs",
            "adminlog",
            "netlog",
            "filePatching",
        }

        parsed = {}
        flags = set()
        custom = []

        for token in args:
            if not token.startswith("-"):
                custom.append(
                    token
                )
                continue

            option = token[1:]

            if "=" in option:
                key, value = option.split(
                    "=",
                    1,
                )

                value = (
                    self._clean_parameter_value(
                        value
                    )
                )

                if key in known_keys:
                    parsed[key] = value
                else:
                    custom.append(
                        token
                    )

            else:
                if option in known_flags:
                    flags.add(
                        option
                    )
                else:
                    custom.append(
                        token
                    )

        self.config_param.setText(
            parsed.get(
                "config",
                "",
            )
        )

        self._set_spinbox_optional(
            self.port_param,
            parsed.get("port"),
        )

        profiles_value = (
            parsed.get(
                "profiles",
                "",
            )
            or "profiles"
        )

        blocker = QSignalBlocker(
            self.profiles_param
        )

        self.profiles_param.setText(
            profiles_value
        )

        del blocker

        self._update_profiles_path(
            emit_signal=True,
            save_config=False,
            profiles_value=profiles_value,
        )

        self.bepath_param.setText(
            parsed.get(
                "BEpath",
                "",
            )
        )

        self._set_spinbox_optional(
            self.cpu_count_param,
            parsed.get("cpuCount"),
        )

        self._set_spinbox_optional(
            self.limit_fps_param,
            parsed.get("limitFPS"),
        )

        self.mod_param.setPlainText(
            parsed.get(
                "mod",
                "",
            )
        )

        self.servermod_param.setPlainText(
            parsed.get(
                "servermod",
                "",
            )
        )

        self.freezecheck_param.setChecked(
            "freezecheck" in flags
        )

        self.dologs_param.setChecked(
            "dologs" in flags
        )

        self.adminlog_param.setChecked(
            "adminlog" in flags
        )

        self.netlog_param.setChecked(
            "netlog" in flags
        )

        self.file_patching_param.setChecked(
            "filePatching" in flags
        )

        self.custom_param.setPlainText(
            "\n".join(custom)
        )

        self._append_output(
            "Launch parameters loaded from ExecStart."
        )

        self._append_output(
            "Resolved Profiles directory: "
            + self.config.profiles_dir
        )

    # ------------------------------------------------------------------
    # Build ExecStart
    # ------------------------------------------------------------------

    def _parameter_lines(self):
        args = []

        config_value = (
            self.config_param.text().strip()
        )

        if config_value:
            args.append(
                f"-config={config_value}"
            )

        if self.port_param.value() != 0:
            args.append(
                f"-port={self.port_param.value()}"
            )

        profiles_value = (
            self.profiles_param.text().strip()
            or "profiles"
        )

        args.append(
            f"-profiles={profiles_value}"
        )

        bepath_value = (
            self.bepath_param.text().strip()
        )

        if bepath_value:
            args.append(
                f"-BEpath={bepath_value}"
            )

        if self.cpu_count_param.value() != 0:
            args.append(
                f"-cpuCount={self.cpu_count_param.value()}"
            )

        if self.limit_fps_param.value() != 0:
            args.append(
                f"-limitFPS={self.limit_fps_param.value()}"
            )

        if self.freezecheck_param.isChecked():
            args.append(
                "-freezecheck"
            )

        if self.dologs_param.isChecked():
            args.append(
                "-dologs"
            )

        if self.adminlog_param.isChecked():
            args.append(
                "-adminlog"
            )

        if self.netlog_param.isChecked():
            args.append(
                "-netlog"
            )

        if self.file_patching_param.isChecked():
            args.append(
                "-filePatching"
            )

        mod_value = (
            self.mod_param
            .toPlainText()
            .strip()
        )

        if mod_value:
            mod_value = re.sub(
                r"\s+",
                "",
                mod_value,
            )

            args.append(
                f'"-mod={mod_value}"'
            )

        servermod_value = (
            self.servermod_param
            .toPlainText()
            .strip()
        )

        if servermod_value:
            servermod_value = re.sub(
                r"\s+",
                "",
                servermod_value,
            )

            args.append(
                f'"-servermod={servermod_value}"'
            )

        return args

    def _custom_parameter_tokens(self):
        custom_text = (
            self.custom_param.toPlainText()
        )

        tokens = []

        for line in custom_text.splitlines():
            line = line.strip()

            if not line:
                continue

            try:
                tokens.extend(
                    shlex.split(
                        line,
                        posix=True,
                    )
                )
            except ValueError:
                tokens.append(
                    line
                )

        return tokens

    def apply_parameters_to_editor(
        self,
    ):
        text = self.editor.toPlainText()

        if not text.strip():
            QMessageBox.warning(
                self,
                "Empty Systemd Unit",
                "Load or enter a systemd unit first.",
            )
            return

        command = self._get_execstart(
            text
        )

        if not command:
            QMessageBox.warning(
                self,
                "ExecStart Not Found",
                "No ExecStart= line was found in the systemd unit.",
            )
            return

        tokens = self._parse_execstart_tokens(
            command
        )

        if not tokens:
            QMessageBox.warning(
                self,
                "Invalid ExecStart",
                "The ExecStart= command could not be parsed.",
            )
            return

        executable = tokens[0]

        custom_from_ui = (
            self._custom_parameter_tokens()
        )

        final_args = []

        final_args.extend(
            self._parameter_lines()
        )

        final_args.extend(
            custom_from_ui
        )

        new_execstart = (
            "ExecStart="
            + executable
        )

        if final_args:
            new_execstart += " "
            new_execstart += " ".join(
                final_args
            )

        lines = text.splitlines()

        new_lines = []
        replaced = False
        skip_continuation = False

        for line in lines:
            stripped = line.strip()

            if skip_continuation:
                if not stripped.endswith(
                    "\\"
                ):
                    skip_continuation = False

                continue

            if stripped.startswith(
                "ExecStart="
            ):
                new_lines.append(
                    new_execstart
                )

                replaced = True

                if stripped.endswith(
                    "\\"
                ):
                    skip_continuation = True

                continue

            new_lines.append(
                line
            )

        if not replaced:
            QMessageBox.warning(
                self,
                "ExecStart Not Found",
                "No ExecStart= line was found.",
            )
            return

        self.editor.setPlainText(
            "\n".join(new_lines)
        )

        profiles_value = (
            self.profiles_param.text().strip()
            or "profiles"
        )

        self._update_profiles_path(
            emit_signal=True,
            save_config=False,
            profiles_value=profiles_value,
        )

        self._append_output(
            "Parameters applied to ExecStart. "
            "Review the unit above before saving."
        )

        self._append_output(
            "Resolved Profiles directory: "
            + self.config.profiles_dir
        )

    # ------------------------------------------------------------------
    # Scheduled restart
    # ------------------------------------------------------------------

    def _timer_unit_names(self):
        service = self._service_name()

        return (
            f"{service}-restart.service",
            f"{service}-restart.timer",
        )

    def apply_restart_timer(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the server first.",
            )
            return

        time_value = (
            self.timer_time_edit.text().strip()
        )

        if not re.match(
            r"^(?:[01]\d|2[0-3]):[0-5]\d$",
            time_value,
        ):
            QMessageBox.warning(
                self,
                "Invalid Time",
                "Enter a valid time in HH:MM format.",
            )
            return

        password = self._sudo_password()

        if not password:
            QMessageBox.warning(
                self,
                "Sudo Password Required",
                "Enter the sudo password on the Server Status tab first.",
            )
            return

        service_unit, timer_unit = (
            self._timer_unit_names()
        )

        service_name = (
            self._service_name()
        )

        service_content = (
            "[Unit]\n"
            f"Description=Restart {service_name} service\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart=/usr/bin/systemctl restart "
            f"{service_name}.service\n"
        )

        timer_content = (
            "[Unit]\n"
            f"Description=Daily restart timer for "
            f"{service_name}\n\n"
            "[Timer]\n"
            f"OnCalendar=*-*-* {time_value}:00\n"
            "Persistent=true\n"
            f"Unit={service_unit}\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )

        service_path = (
            f"/etc/systemd/system/"
            f"{service_unit}"
        )

        timer_path = (
            f"/etc/systemd/system/"
            f"{timer_unit}"
        )

        def work():
            service_tmp = (
                f"/tmp/{service_unit}"
            )

            timer_tmp = (
                f"/tmp/{timer_unit}"
            )

            self.ssh.write_file(
                service_tmp,
                service_content,
                backup=False,
            )

            self.ssh.write_file(
                timer_tmp,
                timer_content,
                backup=False,
            )

            command = (
                "cp -f "
                + shlex.quote(service_tmp)
                + " "
                + shlex.quote(service_path)
                + " && "
                + "cp -f "
                + shlex.quote(timer_tmp)
                + " "
                + shlex.quote(timer_path)
                + " && "
                + "rm -f "
                + shlex.quote(service_tmp)
                + " "
                + shlex.quote(timer_tmp)
                + " && "
                + "systemctl daemon-reload"
                + " && "
                + "systemctl enable --now "
                + shlex.quote(timer_unit)
            )

            return self.ssh.exec_sudo(
                "sh -c "
                + shlex.quote(command),
                password,
            )

        def ok(result):
            code, out, err = result

            if code != 0:
                self._append_output(
                    "Timer setup failed:\n"
                    + (err or out)
                )

                QMessageBox.critical(
                    self,
                    "Timer Failed",
                    err
                    or out
                    or "Unknown error.",
                )
                return

            self._append_output(
                f"Restart timer installed for "
                f"{time_value}."
            )

        def fail(error):
            self._append_output(
                f"Timer setup failed: {error}"
            )

            QMessageBox.critical(
                self,
                "Timer Failed",
                error,
            )

        self.jobs.start(
            work,
            on_ok=ok,
            on_fail=fail,
        )

    def remove_restart_timer(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the server first.",
            )
            return

        password = self._sudo_password()

        if not password:
            QMessageBox.warning(
                self,
                "Sudo Password Required",
                "Enter the sudo password on the Server Status tab first.",
            )
            return

        service_unit, timer_unit = (
            self._timer_unit_names()
        )

        service_path = (
            f"/etc/systemd/system/"
            f"{service_unit}"
        )

        timer_path = (
            f"/etc/systemd/system/"
            f"{timer_unit}"
        )

        command = (
            "systemctl disable --now "
            + shlex.quote(timer_unit)
            + " 2>/dev/null || true; "
            "rm -f "
            + shlex.quote(timer_path)
            + " "
            + shlex.quote(service_path)
            + "; "
            "systemctl daemon-reload"
        )

        def work():
            return self.ssh.exec_sudo(
                "sh -c "
                + shlex.quote(command),
                password,
            )

        def ok(result):
            code, out, err = result

            if code != 0:
                self._append_output(
                    "Remove timer failed:\n"
                    + (err or out)
                )

                QMessageBox.critical(
                    self,
                    "Remove Timer Failed",
                    err
                    or out
                    or "Unknown error.",
                )
                return

            self._append_output(
                "Restart timer removed."
            )

        def fail(error):
            self._append_output(
                f"Remove timer failed: {error}"
            )

            QMessageBox.critical(
                self,
                "Remove Timer Failed",
                error,
            )

        self.jobs.start(
            work,
            on_ok=ok,
            on_fail=fail,
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self):
        self.jobs.shutdown()
