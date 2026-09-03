import re
import shlex
from datetime import datetime

from PySide6.QtCore import Qt, QRegularExpression
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from worker import WorkerRegistry


class LogSearchHighlighter(QSyntaxHighlighter):
    """
    Highlights every occurrence of the current search text.
    """

    def __init__(self, document):
        super().__init__(document)

        self.search_text = ""

        self.format = QTextCharFormat()
        self.format.setBackground(
            QColor("#665c00")
        )
        self.format.setForeground(
            QColor("#ffffff")
        )

    def set_search_text(self, text):
        self.search_text = text or ""
        self.rehighlight()

    def highlightBlock(self, text):
        if not self.search_text:
            return

        expression = QRegularExpression(
            QRegularExpression.escape(
                self.search_text
            )
        )

        iterator = expression.globalMatch(text)

        while iterator.hasNext():
            match = iterator.next()

            self.setFormat(
                match.capturedStart(),
                match.capturedLength(),
                self.format,
            )


class LogViewerPanel(QWidget):
    """
    Historical DayZ log viewer.

    This panel is deliberately separate from StatusPanel's live
    journalctl log viewer.

    Logs are discovered recursively below the directory supplied
    by the DayZ systemd -profiles= parameter.

    Clear Logs is also handled here. It independently inspects
    the systemd service so this panel does not need to depend on
    StatusPanel's live server-state polling.
    """

    MAX_DISPLAY_LINES = 5000

    TEXT_EXTENSIONS = {
        ".rpt",
        ".log",
        ".adm",
        ".txt",
    }

    SPECIAL_LOG_NAMES = {
        "scripts.log",
        "fatal.log",
        "errors.log",
    }

    CLEAR_EXTENSIONS = (
        ".log",
        ".rpt",
        ".mdmp",
        ".adm",
        ".crash",
    )

    def __init__(self, ssh, config):
        super().__init__()

        self.ssh = ssh
        self.config = config

        self.jobs = WorkerRegistry()

        self.connected = False
        self.profiles_path = ""

        self.log_files = []
        self.current_log_path = None

        self._build_ui()

        self.set_connected(
            self.ssh.is_connected()
        )

    # ========================================================
    # UI
    # ========================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ----------------------------------------------------
        # LOG FILE
        # ----------------------------------------------------

        file_group = QGroupBox(
            "Historical Log"
        )

        file_layout = QVBoxLayout(
            file_group
        )

        file_row = QHBoxLayout()

        file_row.addWidget(
            QLabel("Log file:")
        )

        self.log_combo = QComboBox()

        self.log_combo.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )

        self.log_combo.currentIndexChanged.connect(
            self._log_selection_changed
        )

        file_row.addWidget(
            self.log_combo
        )

        self.refresh_button = QPushButton(
            "Refresh"
        )

        self.refresh_button.clicked.connect(
            self.refresh_logs
        )

        file_row.addWidget(
            self.refresh_button
        )

        self.clear_logs_button = QPushButton(
            "Clear Logs"
        )

        self.clear_logs_button.clicked.connect(
            self.clear_logs
        )

        file_row.addWidget(
            self.clear_logs_button
        )

        file_layout.addLayout(
            file_row
        )

        layout.addWidget(
            file_group
        )

        # ----------------------------------------------------
        # SEARCH
        # ----------------------------------------------------

        search_group = QGroupBox(
            "Search"
        )

        search_layout = QHBoxLayout(
            search_group
        )

        self.search_edit = QLineEdit()

        self.search_edit.setPlaceholderText(
            "Search in loaded log..."
        )

        self.search_edit.textChanged.connect(
            self._update_search
        )

        search_layout.addWidget(
            self.search_edit
        )

        self.previous_button = QPushButton(
            "Previous"
        )

        self.previous_button.clicked.connect(
            self.find_previous
        )

        search_layout.addWidget(
            self.previous_button
        )

        self.next_button = QPushButton(
            "Next"
        )

        self.next_button.clicked.connect(
            self.find_next
        )

        search_layout.addWidget(
            self.next_button
        )

        self.clear_search_button = QPushButton(
            "Clear"
        )

        self.clear_search_button.clicked.connect(
            self.clear_search
        )

        search_layout.addWidget(
            self.clear_search_button
        )

        layout.addWidget(
            search_group
        )

        # ----------------------------------------------------
        # LOG OUTPUT
        # ----------------------------------------------------

        self.log_output = QPlainTextEdit()

        self.log_output.setReadOnly(
            True
        )

        self.log_output.setLineWrapMode(
            QPlainTextEdit.NoWrap
        )

        self.log_output.setMaximumBlockCount(
            self.MAX_DISPLAY_LINES
        )

        font = QFont(
            "Monospace"
        )

        font.setStyleHint(
            QFont.TypeWriter
        )

        self.log_output.setFont(
            font
        )

        self.search_highlighter = LogSearchHighlighter(
            self.log_output.document()
        )

        layout.addWidget(
            self.log_output,
            1,
        )

        # ----------------------------------------------------
        # FILE INFORMATION
        # ----------------------------------------------------

        self.file_info_label = QLabel(
            "No log loaded."
        )

        self.file_info_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )

        layout.addWidget(
            self.file_info_label
        )

    # ========================================================
    # CONNECTION STATE
    # ========================================================

    def set_connected(self, connected):
        self.connected = bool(
            connected
        )

        self.refresh_button.setEnabled(
            self.connected
        )

        self.clear_logs_button.setEnabled(
            self.connected
            and bool(self.profiles_path)
        )

        self.log_combo.setEnabled(
            self.connected
        )

        self.search_edit.setEnabled(
            self.connected
        )

        self.previous_button.setEnabled(
            self.connected
        )

        self.next_button.setEnabled(
            self.connected
        )

        self.clear_search_button.setEnabled(
            self.connected
        )

        if not self.connected:
            self.log_combo.clear()
            self.log_files.clear()
            self.current_log_path = None
            self.log_output.clear()
            self.file_info_label.setText(
                "Not connected."
            )
            return

        if self.profiles_path:
            self.refresh_logs()

    # ========================================================
    # PROFILES PATH
    # ========================================================

    def set_profiles_path(self, path):
        """
        Called by MainWindow when SystemdPanel resolves the
        authoritative -profiles= path.
        """

        path = (path or "").strip()

        if path == self.profiles_path:
            if (
                self.connected
                and self.profiles_path
            ):
                self.clear_logs_button.setEnabled(
                    True
                )
            return

        self.profiles_path = path

        self.clear_logs_button.setEnabled(
            self.connected
            and bool(self.profiles_path)
        )

        if self.connected and self.profiles_path:
            self.refresh_logs()

    # ========================================================
    # DISCOVERY
    # ========================================================

    def refresh_logs(self):
        if not self.connected:
            return

        if not self.profiles_path:
            self.log_combo.clear()
            self.log_files.clear()
            self.log_output.clear()

            self.file_info_label.setText(
                "No -profiles= directory is configured."
            )

            self.clear_logs_button.setEnabled(
                False
            )

            return

        profiles_path = self.profiles_path

        self.refresh_button.setEnabled(
            False
        )

        self.clear_logs_button.setEnabled(
            False
        )

        def fetch():
            return self._discover_logs(
                profiles_path
            )

        self.jobs.start(
            fetch,
            on_ok=self._on_logs_discovered,
            on_fail=self._on_discovery_error,
        )

    def _discover_logs(self, profiles_path):
        """
        Recursively discover text log files beneath -profiles=.

        Output format:

            relative_path<TAB>size<TAB>mtime<TAB>extension
        """

        root = profiles_path.rstrip("/")

        command = (
            "find "
            + shlex.quote(root)
            + " -type f "
            "\\( "
            "-iname '*.rpt' "
            "-o -iname '*.log' "
            "-o -iname '*.adm' "
            "-o -iname '*.txt' "
            "\\) "
            "-printf '%s\\t%T@\\t%p\\n' "
            "| sort -k2,2nr"
        )

        exit_code, stdout, stderr = self.ssh.exec(
            command,
            timeout=30,
        )

        if exit_code != 0:
            raise RuntimeError(
                stderr.strip()
                or "Failed to discover log files."
            )

        results = []

        for line in stdout.splitlines():
            parts = line.split(
                "\t",
                2,
            )

            if len(parts) != 3:
                continue

            size_text, mtime_text, path = parts

            try:
                size = int(
                    size_text
                )

                mtime = float(
                    mtime_text
                )
            except ValueError:
                continue

            if not path.startswith(
                root + "/"
            ):
                continue

            relative_path = path[
                len(root) + 1:
            ]

            results.append(
                {
                    "path": path,
                    "relative_path": relative_path,
                    "size": size,
                    "mtime": mtime,
                }
            )

        return results

    def _on_logs_discovered(self, files):
        self.refresh_button.setEnabled(
            self.connected
        )

        self.clear_logs_button.setEnabled(
            self.connected
            and bool(self.profiles_path)
        )

        previous_path = (
            self.current_log_path
        )

        self.log_files = files

        self.log_combo.blockSignals(
            True
        )

        self.log_combo.clear()

        for entry in files:
            self.log_combo.addItem(
                self._display_path(
                    entry["relative_path"]
                ),
                entry["path"],
            )

        self.log_combo.blockSignals(
            False
        )

        if not files:
            self.current_log_path = None
            self.log_output.clear()

            self.file_info_label.setText(
                "No supported text log files found."
            )

            return

        index = 0

        if previous_path:
            for i, entry in enumerate(
                files
            ):
                if entry["path"] == previous_path:
                    index = i
                    break

        self.log_combo.setCurrentIndex(
            index
        )

        self._load_selected_log()

    def _on_discovery_error(self, message):
        self.refresh_button.setEnabled(
            self.connected
        )

        self.clear_logs_button.setEnabled(
            self.connected
            and bool(self.profiles_path)
        )

        self.file_info_label.setText(
            f"Log discovery failed: {message}"
        )

    # ========================================================
    # FILE SELECTION
    # ========================================================

    def _log_selection_changed(self, index):
        if index < 0:
            return

        self._load_selected_log()

    def _load_selected_log(self):
        index = self.log_combo.currentIndex()

        if index < 0:
            return

        if index >= len(
            self.log_files
        ):
            return

        entry = self.log_files[index]

        path = entry["path"]

        self.current_log_path = path

        self.log_output.setPlainText(
            "Loading log..."
        )

        self.file_info_label.setText(
            "Loading..."
        )

        def fetch():
            return self._read_log(
                path
            )

        self.jobs.start(
            fetch,
            on_ok=self._on_log_loaded,
            on_fail=self._on_log_load_error,
        )

    def _read_log(self, path):
        quoted_path = shlex.quote(
            path
        )

        line_command = (
            "wc -l < "
            + quoted_path
        )

        exit_code, stdout, stderr = self.ssh.exec(
            line_command,
            timeout=30,
        )

        if exit_code != 0:
            raise RuntimeError(
                stderr.strip()
                or "Unable to determine log size."
            )

        try:
            total_lines = int(
                stdout.strip()
            )
        except ValueError:
            total_lines = None

        tail_command = (
            "tail -n "
            + str(self.MAX_DISPLAY_LINES)
            + " -- "
            + quoted_path
        )

        exit_code, stdout, stderr = self.ssh.exec(
            tail_command,
            timeout=60,
        )

        if exit_code != 0:
            raise RuntimeError(
                stderr.strip()
                or "Unable to read log."
            )

        stat_command = (
            "stat -c '%s\\t%Y' "
            + quoted_path
        )

        exit_code, stat_stdout, _ = self.ssh.exec(
            stat_command,
            timeout=15,
        )

        size = None
        mtime = None

        if exit_code == 0:
            parts = stat_stdout.strip().split(
                "\t",
                1,
            )

            if len(parts) == 2:
                try:
                    size = int(parts[0])
                    mtime = float(parts[1])
                except ValueError:
                    pass

        return {
            "path": path,
            "content": stdout,
            "total_lines": total_lines,
            "size": size,
            "mtime": mtime,
        }

    def _on_log_loaded(self, result):
        self.current_log_path = result["path"]

        self.log_output.setPlainText(
            result["content"]
        )

        self.search_highlighter.set_search_text(
            self.search_edit.text()
        )

        relative_path = self._relative_path(
            result["path"]
        )

        size = result["size"]

        if size is None:
            size = 0

        total_lines = result[
            "total_lines"
        ]

        if total_lines is None:
            line_text = "unknown lines"
        else:
            shown = min(
                total_lines,
                self.MAX_DISPLAY_LINES,
            )

            if total_lines > shown:
                line_text = (
                    f"showing last {shown:,} "
                    f"of {total_lines:,} lines"
                )
            else:
                line_text = (
                    f"{total_lines:,} lines"
                )

        modified = result["mtime"]

        if modified is not None:
            modified_text = datetime.fromtimestamp(
                modified
            ).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            modified_text = "unknown"

        self.file_info_label.setText(
            f"File: {relative_path}    "
            f"Size: {self._format_size(size)}    "
            f"Modified: {modified_text}    "
            f"{line_text}"
        )

        self._scroll_to_bottom()

    def _on_log_load_error(self, message):
        self.log_output.clear()

        self.file_info_label.setText(
            f"Failed to load log: {message}"
        )

    # ========================================================
    # CLEAR LOGS
    # ========================================================

    def clear_logs(self):
        """
        Safely clear DayZ logs while the service is stopped.

        StatusPanel no longer owns this functionality.

        The service is inspected in the background before the
        confirmation dialog. The deletion worker then performs
        the inspection again and checks the service state
        immediately before deletion.
        """

        if not self.connected:
            QMessageBox.information(
                self,
                "Not connected",
                "Click Connect first.",
            )
            return

        service_name = getattr(
            self.config,
            "systemd_service",
            "",
        )

        if not service_name:
            QMessageBox.warning(
                self,
                "Clear Logs",
                "No systemd service is configured.",
            )
            return

        self.clear_logs_button.setEnabled(
            False
        )

        self.refresh_button.setEnabled(
            False
        )

        self.file_info_label.setText(
            "Checking server state..."
        )

        def preflight():
            return self._inspect_service(
                service_name
            )

        self.jobs.start(
            preflight,
            on_ok=self._on_clear_logs_preflight,
            on_fail=self._on_clear_logs_error,
        )

    def _inspect_service(self, service_name):
        """
        Inspect the actual systemd service and determine its
        -profiles= directory.
        """

        inspect_command = (
            "systemctl show "
            + shlex.quote(service_name)
            + " -p ExecStart -p WorkingDirectory --no-pager"
        )

        inspect_code, inspect_out, inspect_err = (
            self.ssh.exec(
                inspect_command,
                timeout=30,
            )
        )

        if inspect_code != 0:
            raise RuntimeError(
                inspect_err.strip()
                or inspect_out.strip()
                or (
                    "Failed to inspect the systemd service "
                    f"{service_name}."
                )
            )

        exec_start = ""
        working_directory = ""

        for line in inspect_out.splitlines():
            if line.startswith("ExecStart="):
                exec_start = (
                    line[len("ExecStart="):]
                    .strip()
                )

            elif line.startswith(
                "WorkingDirectory="
            ):
                working_directory = (
                    line[len("WorkingDirectory="):]
                    .strip()
                )

        if not exec_start:
            raise RuntimeError(
                "Could not determine ExecStart for "
                f"{service_name}."
            )

        profiles_match = re.search(
            r"(?:^|\s)-profiles="
            r"(\"[^\"]*\"|'[^']*'|\S+)",
            exec_start,
        )

        if not profiles_match:
            raise RuntimeError(
                "Could not find -profiles= in the service's "
                "ExecStart.\n\n"
                f"ExecStart:\n{exec_start}"
            )

        profiles_path = (
            profiles_match.group(1).strip()
        )

        if (
            len(profiles_path) >= 2
            and profiles_path[0] == profiles_path[-1]
            and profiles_path[0] in (
                "'",
                '"',
            )
        ):
            profiles_path = profiles_path[1:-1]

        if not profiles_path.startswith("/"):
            if not working_directory:
                raise RuntimeError(
                    "The service uses a relative "
                    "-profiles= path, but no "
                    "WorkingDirectory was found."
                )

            profiles_path = (
                working_directory.rstrip("/")
                + "/"
                + profiles_path.lstrip("/")
            )

        normalize_command = (
            "realpath -m "
            + shlex.quote(profiles_path)
        )

        normalize_code, normalize_out, normalize_err = (
            self.ssh.exec(
                normalize_command,
                timeout=15,
            )
        )

        if (
            normalize_code == 0
            and normalize_out.strip()
        ):
            profiles_path = (
                normalize_out.strip()
            )

        check_command = (
            "test -d "
            + shlex.quote(profiles_path)
        )

        check_code, check_out, check_err = (
            self.ssh.exec(
                check_command,
                timeout=15,
            )
        )

        if check_code != 0:
            raise RuntimeError(
                "The configured profiles directory does not exist:\n\n"
                f"{profiles_path}\n\n"
                f"ExecStart:\n{exec_start}"
            )

        state_command = (
            "systemctl is-active "
            + shlex.quote(service_name)
        )

        state_code, state_out, state_err = (
            self.ssh.exec(
                state_command,
                timeout=15,
            )
        )

        state = (
            state_out.strip()
            or state_err.strip()
            or "unknown"
        )

        if state not in (
            "inactive",
            "failed",
        ):
            return {
                "allowed": False,
                "state": state,
                "profiles_path": profiles_path,
                "exec_start": exec_start,
                "service_name": service_name,
            }

        return {
            "allowed": True,
            "state": state,
            "profiles_path": profiles_path,
            "exec_start": exec_start,
            "service_name": service_name,
        }

    def _on_clear_logs_preflight(self, result):
        if not self.connected:
            self._restore_clear_logs_buttons()
            return

        if not result["allowed"]:
            self._restore_clear_logs_buttons()

            QMessageBox.warning(
                self,
                "Server is running",
                (
                    "Logs can only be cleared while the DayZ "
                    "server is stopped.\n\n"
                    "Current service state: "
                    f"{result['state']}"
                ),
            )
            return

        response = QMessageBox.question(
            self,
            "Confirm",
            (
                "The server's actual -profiles= directory will "
                "be detected from the systemd service.\n\n"
                "The server must remain stopped while this "
                "operation runs.\n\n"
                "All supported log files in that directory and "
                "its subfolders will be deleted.\n\n"
                "This includes:\n"
                "  .log\n"
                "  .rpt\n"
                "  .mdmp\n"
                "  .adm\n"
                "  .crash\n\n"
                f"Profiles directory:\n"
                f"{result['profiles_path']}\n\n"
                "Continue?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if response != QMessageBox.Yes:
            self._restore_clear_logs_buttons()
            return

        self.file_info_label.setText(
            "Clearing server logs..."
        )

        service_name = result[
            "service_name"
        ]

        def task():
            return self._clear_logs_task(
                service_name
            )

        self.jobs.start(
            task,
            on_ok=self._on_clear_logs_result,
            on_fail=self._on_clear_logs_error,
        )

    def _clear_logs_task(self, service_name):
        """
        Perform the actual clear operation.

        The service and profiles directory are inspected again
        rather than trusting the preflight result.

        A final systemd state check is performed immediately
        before find/delete.
        """

        service_info = self._inspect_service(
            service_name
        )

        if not service_info["allowed"]:
            raise RuntimeError(
                "The DayZ server is no longer in a stopped "
                "state.\n\n"
                "No log files were deleted.\n\n"
                "Current service state: "
                f"{service_info['state']}"
            )

        profiles_path = service_info[
            "profiles_path"
        ]

        state_check = (
            "state=$(systemctl is-active "
            + shlex.quote(service_name)
            + "); "
            "if [ \"$state\" != \"inactive\" ] "
            "&& [ \"$state\" != \"failed\" ]; then "
            "echo \"SERVER_RUNNING:$state\" >&2; "
            "exit 2; "
            "fi; "
        )

        clear_find = (
            "find "
            + shlex.quote(profiles_path)
            + " -type f \\( "
            + "-iname '*.log' "
            + "-o -iname '*.rpt' "
            + "-o -iname '*.mdmp' "
            + "-o -iname '*.adm' "
            + "-o -iname '*.crash' "
            + "\\) -print -delete"
        )

        clear_command = (
            state_check
            + clear_find
        )

        clear_code, clear_out, clear_err = (
            self.ssh.exec(
                clear_command,
                timeout=120,
            )
        )

        if clear_code == 2:
            raise RuntimeError(
                "The DayZ server is no longer in a stopped "
                "state.\n\n"
                "No log files were deleted.\n\n"
                + (
                    clear_err.strip()
                    or "The server state changed."
                )
            )

        if clear_code != 0:
            raise RuntimeError(
                clear_err.strip()
                or clear_out.strip()
                or "Failed to clear server logs."
            )

        deleted_files = [
            line.strip()
            for line in clear_out.splitlines()
            if line.strip()
        ]

        return (
            profiles_path,
            deleted_files,
        )

    def _on_clear_logs_result(self, result):
        self._restore_clear_logs_buttons()

        profiles_path, deleted_files = result

        deleted_count = len(
            deleted_files
        )

        QMessageBox.information(
            self,
            "Clear Logs",
            (
                "Server logs were cleared successfully.\n\n"
                f"Profiles directory:\n{profiles_path}\n\n"
                f"Deleted {deleted_count} log file(s), "
                "including logs in subfolders."
            ),
        )

        self.current_log_path = None

        self.refresh_logs()

    def _on_clear_logs_error(self, message):
        self._restore_clear_logs_buttons()

        self.file_info_label.setText(
            f"Clear Logs failed: {message}"
        )

        QMessageBox.warning(
            self,
            "Clear Logs",
            str(message),
        )

    def _restore_clear_logs_buttons(self):
        self.refresh_button.setEnabled(
            self.connected
        )

        self.clear_logs_button.setEnabled(
            self.connected
            and bool(self.profiles_path)
        )

    # ========================================================
    # SEARCH
    # ========================================================

    def _update_search(self, text):
        self.search_highlighter.set_search_text(
            text
        )

        if text:
            self._find_match(
                text,
                forward=True,
                wrap=True,
            )

    def clear_search(self):
        self.search_edit.clear()

        self.search_highlighter.set_search_text(
            ""
        )

        self._scroll_to_bottom()

    def find_next(self):
        text = self.search_edit.text()

        if not text:
            return

        self._find_match(
            text,
            forward=True,
            wrap=True,
        )

    def find_previous(self):
        text = self.search_edit.text()

        if not text:
            return

        self._find_match(
            text,
            forward=False,
            wrap=True,
        )

    def _find_match(
        self,
        text,
        forward=True,
        wrap=True,
    ):
        document = (
            self.log_output.document()
        )

        cursor = (
            self.log_output.textCursor()
        )

        if forward:
            start_position = (
                cursor.selectionEnd()
            )

            if start_position < 0:
                start_position = 0

            found_cursor = document.find(
                text,
                start_position,
            )

            if found_cursor.isNull() and wrap:
                found_cursor = document.find(
                    text,
                    0,
                )

        else:
            start_position = (
                cursor.selectionStart()
            )

            if start_position < 0:
                start_position = (
                    document.characterCount()
                )

            found_cursor = document.find(
                text,
                start_position,
                QTextDocument.FindBackward,
            )

            if found_cursor.isNull() and wrap:
                found_cursor = document.find(
                    text,
                    document.characterCount(),
                    QTextDocument.FindBackward,
                )

        if not found_cursor.isNull():
            self.log_output.setTextCursor(
                found_cursor
            )

            self.log_output.ensureCursorVisible()

    # ========================================================
    # HELPERS
    # ========================================================

    def _scroll_to_bottom(self):
        cursor = (
            self.log_output.textCursor()
        )

        cursor.clearSelection()

        cursor.movePosition(
            QTextCursor.End
        )

        self.log_output.setTextCursor(
            cursor
        )

        self.log_output.ensureCursorVisible()

        scrollbar = (
            self.log_output.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )

    def _relative_path(self, path):
        root = self.profiles_path.rstrip("/")

        if path.startswith(
            root + "/"
        ):
            return path[
                len(root) + 1:
            ]

        return path

    @staticmethod
    def _display_path(path):
        return path

    @staticmethod
    def _format_size(size):
        size = float(size)

        units = (
            "B",
            "KB",
            "MB",
            "GB",
            "TB",
        )

        for unit in units:
            if size < 1024:
                return f"{size:.1f} {unit}"

            size /= 1024

        return f"{size:.1f} PB"
