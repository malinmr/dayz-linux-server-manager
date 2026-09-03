import re
import time
from collections import deque

import a2s

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QMessageBox,
    QLineEdit,
    QGroupBox,
    QSizePolicy,
    QToolTip,
)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)

from worker import WorkerRegistry
from widgets.lamp import Lamp


class ServiceStatusHighlighter(QSyntaxHighlighter):
    """Highlights important systemd status information."""

    def __init__(self, document):
        super().__init__(document)

        self.enabled_format = QTextCharFormat()
        self.enabled_format.setForeground(QColor("#4CAF50"))

        font = QFont()
        font.setBold(True)
        self.enabled_format.setFont(font)

        self.disabled_format = QTextCharFormat()
        self.disabled_format.setForeground(QColor("#FF9800"))

        font = QFont()
        font.setBold(True)
        self.disabled_format.setFont(font)

        self.active_format = QTextCharFormat()
        self.active_format.setForeground(QColor("#4CAF50"))

        font = QFont()
        font.setBold(True)
        self.active_format.setFont(font)

        self.transition_format = QTextCharFormat()
        self.transition_format.setForeground(QColor("#FF9800"))

        font = QFont()
        font.setBold(True)
        self.transition_format.setFont(font)

        self.failed_format = QTextCharFormat()
        self.failed_format.setForeground(QColor("#F44336"))

        font = QFont()
        font.setBold(True)
        self.failed_format.setFont(font)

        self.inactive_format = QTextCharFormat()
        self.inactive_format.setForeground(QColor("#9E9E9E"))

        font = QFont()
        font.setBold(True)
        self.inactive_format.setFont(font)

    def highlightBlock(self, text):
        if text.lstrip().startswith("Loaded:"):
            for match in re.finditer(
                r"\benabled\b",
                text,
                re.IGNORECASE,
            ):
                self.setFormat(
                    match.start(),
                    match.end() - match.start(),
                    self.enabled_format,
                )

            for match in re.finditer(
                r"\bdisabled\b",
                text,
                re.IGNORECASE,
            ):
                self.setFormat(
                    match.start(),
                    match.end() - match.start(),
                    self.disabled_format,
                )

        active_match = re.search(
            r"^\s*Active:\s+([a-zA-Z]+)"
            r"(?:\s+\(([^)]+)\))?",
            text,
            re.IGNORECASE,
        )

        if not active_match:
            return

        state = active_match.group(1).lower()
        state_start = active_match.start(1)

        if active_match.group(2):
            state_end = active_match.end(2)
        else:
            state_end = active_match.end(1)

        state_length = state_end - state_start

        if state == "active":
            fmt = self.active_format

        elif state in (
            "activating",
            "deactivating",
        ):
            fmt = self.transition_format

        elif state == "failed":
            fmt = self.failed_format

        elif state == "inactive":
            fmt = self.inactive_format

        else:
            fmt = self.inactive_format

        self.setFormat(
            state_start,
            state_length,
            fmt,
        )


class LogSearchHighlighter(QSyntaxHighlighter):
    """Highlights all occurrences of a search term in the server log."""

    def __init__(self, document):
        super().__init__(document)

        self.search_text = ""

        self.search_format = QTextCharFormat()
        self.search_format.setBackground(QColor("#FFF176"))
        self.search_format.setForeground(QColor("#000000"))

    def set_search_text(self, text):
        self.search_text = text or ""
        self.rehighlight()

    def highlightBlock(self, text):
        if not self.search_text:
            return

        pattern = re.escape(self.search_text)

        for match in re.finditer(
            pattern,
            text,
            re.IGNORECASE,
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.search_format,
            )


class LiveHistoryGraph(QWidget):
    """
    Qt-native live history graph.

    The graph displays four metrics on a shared 0-100 scale:

        Players = percentage of max player slots
        CPU     = process CPU relative to total CPU capacity
        RAM     = system memory percentage
        FPS     = relative to 60 FPS

    The original/raw values are retained for the hover tooltip.
    """

    PLAYER_COLOR = QColor("#4FC3F7")
    CPU_COLOR = QColor("#FFB74D")
    RAM_COLOR = QColor("#BA68C8")
    FPS_COLOR = QColor("#81C784")

    def __init__(self, parent=None):
        super().__init__(parent)

        self.samples = deque(maxlen=3600)

        self.window_minutes = 5

        self.setMinimumHeight(190)

        self.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )

        self.setMouseTracking(True)

    # ----------------------------------------------------------------
    # History management
    # ----------------------------------------------------------------

    def clear_history(self):
        self.samples.clear()
        self.update()

    def set_time_window(self, minutes):
        self.window_minutes = int(minutes)

        self._trim_samples()

        self.update()

    def add_sample(
        self,
        timestamp,
        players=None,
        max_players=None,
        cpu_percent=None,
        cpu_count=None,
        ram_percent=None,
        fps=None,
    ):
        player_value = None

        if (
            players is not None
            and max_players is not None
            and max_players > 0
        ):
            player_value = (
                float(players)
                / float(max_players)
                * 100.0
            )

            player_value = max(
                0.0,
                min(
                    100.0,
                    player_value,
                ),
            )

        cpu_value = None

        if cpu_percent is not None:
            cores = float(
                cpu_count or 1
            )

            if cores <= 0:
                cores = 1.0

            cpu_value = (
                float(cpu_percent)
                / cores
            )

            cpu_value = max(
                0.0,
                min(
                    100.0,
                    cpu_value,
                ),
            )

        ram_value = None

        if ram_percent is not None:
            ram_value = max(
                0.0,
                min(
                    100.0,
                    float(ram_percent),
                ),
            )

        fps_value = None

        if fps is not None:
            fps_value = (
                float(fps)
                / 60.0
                * 100.0
            )

            fps_value = max(
                0.0,
                min(
                    100.0,
                    fps_value,
                ),
            )

        self.samples.append(
            {
                "timestamp": float(timestamp),

                "players": players,
                "max_players": max_players,
                "player_value": player_value,

                "cpu": cpu_percent,
                "cpu_count": cpu_count,
                "cpu_value": cpu_value,

                "ram": ram_percent,
                "ram_value": ram_value,

                "fps": fps,
                "fps_value": fps_value,
            }
        )

        self._trim_samples()

        self.update()

    def _trim_samples(self):
        if not self.samples:
            return

        cutoff = (
            time.time()
            - self.window_minutes * 60
        )

        while (
            self.samples
            and self.samples[0]["timestamp"] < cutoff
        ):
            self.samples.popleft()

    # ----------------------------------------------------------------
    # Painting
    # ----------------------------------------------------------------

    def _plot_rect(self):
        return self.rect().adjusted(
            48,
            34,
            -12,
            -30,
        )

    def _series_points(
        self,
        key,
        plot,
    ):
        if not self.samples:
            return []

        first_time = (
            time.time()
            - self.window_minutes * 60
        )

        last_time = time.time()

        time_span = max(
            1.0,
            last_time - first_time,
        )

        points = []

        for sample in self.samples:
            value = sample.get(key)

            if value is None:
                if (
                    points
                    and points[-1] is not None
                ):
                    points.append(None)

                continue

            x = (
                plot.left()
                + (
                    (
                        sample["timestamp"]
                        - first_time
                    )
                    / time_span
                )
                * plot.width()
            )

            x = max(
                plot.left(),
                min(
                    plot.right(),
                    x,
                ),
            )

            y = (
                plot.bottom()
                - (
                    float(value)
                    / 100.0
                )
                * plot.height()
            )

            y = max(
                plot.top(),
                min(
                    plot.bottom(),
                    y,
                ),
            )

            points.append(
                (
                    int(x),
                    int(y),
                )
            )

        return points

    def _draw_series(
        self,
        painter,
        plot,
        key,
        color,
    ):
        points = self._series_points(
            key,
            plot,
        )

        if not points:
            return

        pen = QPen(color)
        pen.setWidth(2)

        painter.setPen(pen)

        previous = None

        for point in points:
            if point is None:
                previous = None
                continue

            if previous is not None:
                painter.drawLine(
                    previous[0],
                    previous[1],
                    point[0],
                    point[1],
                )

            previous = point

    def _draw_legend(self, painter):
        entries = (
            (
                "Players",
                self.PLAYER_COLOR,
            ),
            (
                "CPU",
                self.CPU_COLOR,
            ),
            (
                "RAM",
                self.RAM_COLOR,
            ),
            (
                "FPS",
                self.FPS_COLOR,
            ),
        )

        x = 52
        y = 18

        font = painter.font()
        font.setPointSize(8)

        painter.setFont(font)

        metrics = painter.fontMetrics()

        text_color = (
            self.palette()
            .text()
            .color()
        )

        for name, color in entries:
            pen = QPen(color)
            pen.setWidth(3)

            painter.setPen(pen)

            painter.drawLine(
                x,
                y - 3,
                x + 14,
                y - 3,
            )

            painter.setPen(
                text_color
            )

            painter.drawText(
                x + 19,
                y,
                name,
            )

            x += (
                metrics.horizontalAdvance(
                    name
                )
                + 45
            )

    def paintEvent(self, event):
        del event

        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.Antialiasing
        )

        background = (
            self.palette()
            .base()
            .color()
        )

        text_color = (
            self.palette()
            .text()
            .color()
        )

        painter.fillRect(
            self.rect(),
            background,
        )

        plot = self._plot_rect()

        if (
            plot.width() <= 0
            or plot.height() <= 0
        ):
            return

        self._draw_legend(
            painter
        )

        grid_color = QColor(
            text_color
        )

        grid_color.setAlpha(45)

        grid_pen = QPen(
            grid_color
        )

        grid_pen.setWidth(1)

        painter.setPen(
            grid_pen
        )

        for percent in (
            0,
            25,
            50,
            75,
            100,
        ):
            y = (
                plot.bottom()
                - (
                    plot.height()
                    * (
                        percent
                        / 100.0
                    )
                )
            )

            painter.drawLine(
                plot.left(),
                int(y),
                plot.right(),
                int(y),
            )

            painter.setPen(
                text_color
            )

            painter.drawText(
                5,
                int(y + 4),
                str(percent),
            )

            painter.setPen(
                grid_pen
            )

        painter.setPen(
            text_color
        )

        painter.drawText(
            plot.left(),
            self.height() - 8,
            f"{self.window_minutes}m ago",
        )

        now_text = "now"

        now_width = (
            painter.fontMetrics()
            .horizontalAdvance(
                now_text
            )
        )

        painter.drawText(
            plot.right() - now_width,
            self.height() - 8,
            now_text,
        )

        self._draw_series(
            painter,
            plot,
            "player_value",
            self.PLAYER_COLOR,
        )

        self._draw_series(
            painter,
            plot,
            "cpu_value",
            self.CPU_COLOR,
        )

        self._draw_series(
            painter,
            plot,
            "ram_value",
            self.RAM_COLOR,
        )

        self._draw_series(
            painter,
            plot,
            "fps_value",
            self.FPS_COLOR,
        )

    # ----------------------------------------------------------------
    # Hover information.
    # ----------------------------------------------------------------

    def mouseMoveEvent(self, event):
        if not self.samples:
            QToolTip.hideText()
            return

        plot = self._plot_rect()

        position = event.position().toPoint()

        if not plot.contains(position):
            QToolTip.hideText()
            return

        fraction = (
            position.x()
            - plot.left()
        ) / max(
            1.0,
            plot.width(),
        )

        target_time = (
            time.time()
            - self.window_minutes * 60
            + fraction
            * self.window_minutes
            * 60
        )

        sample = min(
            self.samples,
            key=lambda item: abs(
                item["timestamp"]
                - target_time
            ),
        )

        timestamp = time.strftime(
            "%H:%M:%S",
            time.localtime(
                sample["timestamp"]
            ),
        )

        lines = [
            timestamp
        ]

        players = sample.get(
            "players"
        )

        max_players = sample.get(
            "max_players"
        )

        if players is not None:
            if max_players is not None:
                lines.append(
                    f"Players: "
                    f"{players}/{max_players}"
                )
            else:
                lines.append(
                    f"Players: {players}"
                )

        cpu = sample.get(
            "cpu"
        )

        if cpu is not None:
            cpu_count = (
                sample.get(
                    "cpu_count"
                )
                or 1
            )

            normalized_cpu = (
                float(cpu)
                / float(cpu_count)
            )

            lines.append(
                f"CPU: {float(cpu):.1f}% "
                f"process "
                f"({normalized_cpu:.1f}% total)"
            )

        ram = sample.get(
            "ram"
        )

        if ram is not None:
            lines.append(
                f"RAM: {float(ram):.1f}%"
            )

        fps = sample.get(
            "fps"
        )

        if fps is not None:
            lines.append(
                f"FPS: {float(fps):.1f}"
            )

        QToolTip.showText(
            event.globalPosition().toPoint(),
            "\n".join(lines),
            self,
        )

    def leaveEvent(self, event):
        QToolTip.hideText()

        super().leaveEvent(event)


class StatusPanel(QWidget):
    """Live DayZ server status/control tab."""

    LIVE_INTERVAL_MS = 1000
    A2S_INTERVAL_MS = 5000

    MAX_LOG_LINES = 500

    def __init__(
        self,
        ssh,
        config,
        on_connection_changed=None,
    ):
        super().__init__()

        self.ssh = ssh
        self.config = config
        self.on_connection_changed = on_connection_changed
        self.jobs = WorkerRegistry()

        self.server_state = "unknown"

        self.refresh_running = False
        self.last_refresh_time = None

        self.a2s_stats = None
        self.a2s_request_running = False
        self.last_a2s_time = 0.0

        self.resource_stats = None

        self.remote_cpu_count = 1

        self.log_search_active = False

        self.history_graph = None

        layout = QVBoxLayout(self)

        # ============================================================
        # Connection / overall status row
        # ============================================================

        indicator_row = QHBoxLayout()

        self.lamp = Lamp(20)

        self.status_label = QLabel(
            "Not connected"
        )

        self.status_label.setStyleSheet(
            "font-size: 16px; font-weight: 600;"
        )

        indicator_row.addWidget(self.lamp)
        indicator_row.addWidget(self.status_label)

        indicator_row.addStretch()

        self.live_label = QLabel(
            "● OFFLINE"
        )

        self.live_label.setStyleSheet(
            "font-size: 13px; "
            "font-weight: 600; "
            "color: #9E9E9E;"
        )

        indicator_row.addWidget(
            self.live_label
        )

        self.update_label = QLabel(
            "Last update: --"
        )

        self.update_label.setStyleSheet(
            "font-family: monospace; "
            "font-size: 12px;"
        )

        indicator_row.addWidget(
            self.update_label
        )

        self.connect_btn = QPushButton(
            "Connect"
        )

        self.connect_btn.clicked.connect(
            self.toggle_connection
        )

        indicator_row.addWidget(
            self.connect_btn
        )

        self.refresh_btn = QPushButton(
            "Refresh"
        )

        self.refresh_btn.clicked.connect(
            self.refresh_status
        )

        indicator_row.addWidget(
            self.refresh_btn
        )

        layout.addLayout(indicator_row)

        # ============================================================
        # Sudo password
        # ============================================================

        sudo_row = QHBoxLayout()

        sudo_row.addWidget(
            QLabel(
                "Sudo password (for Start/Restart/Stop/Enable/Disable; "
                "kept in memory only, never saved):"
            )
        )

        self.sudo_password_edit = QLineEdit()

        self.sudo_password_edit.setEchoMode(
            QLineEdit.Password
        )

        self.sudo_password_edit.setPlaceholderText(
            "leave blank if passwordless sudo is already configured"
        )

        sudo_row.addWidget(
            self.sudo_password_edit
        )

        layout.addLayout(sudo_row)

        # ============================================================
        # Service controls
        # ============================================================

        btn_row = QHBoxLayout()

        self.start_btn = QPushButton("Start")
        self.restart_btn = QPushButton("Restart")
        self.stop_btn = QPushButton("Stop")
        self.enable_btn = QPushButton("Enable")
        self.disable_btn = QPushButton("Disable")

        self.start_btn.clicked.connect(
            lambda: self.run_action("start")
        )

        self.restart_btn.clicked.connect(
            lambda: self.run_action("restart")
        )

        self.stop_btn.clicked.connect(
            lambda: self.run_action("stop")
        )

        self.enable_btn.clicked.connect(
            lambda: self.run_action("enable")
        )

        self.disable_btn.clicked.connect(
            lambda: self.run_action("disable")
        )

        for button in (
            self.start_btn,
            self.restart_btn,
            self.stop_btn,
            self.enable_btn,
            self.disable_btn,
        ):
            btn_row.addWidget(button)

        layout.addLayout(btn_row)

        # ============================================================
        # Systemd service status box
        # ============================================================

        status_group = QGroupBox(
            "Service Status — LIVE"
        )

        status_layout = QVBoxLayout(
            status_group
        )

        self.status_output = QPlainTextEdit()

        self.status_output.setReadOnly(True)

        self.status_output.setLineWrapMode(
            QPlainTextEdit.NoWrap
        )

        self.status_output.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        self.status_output.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )

        self.status_output.setStyleSheet(
            "font-family: monospace;"
        )

        self.status_output.setMinimumHeight(230)
        self.status_output.setMaximumHeight(230)

        self.status_highlighter = (
            ServiceStatusHighlighter(
                self.status_output.document()
            )
        )

        status_layout.addWidget(
            self.status_output
        )

        layout.addWidget(status_group)

        # ============================================================
        # Resource monitor
        # ============================================================

        resource_group = QGroupBox(
            "Server Resources — LIVE"
        )

        resource_layout = QVBoxLayout(
            resource_group
        )

        self.resource_status_label = QLabel(
            "CPU: --    RAM: --    PID: --    Uptime: --"
        )

        self.resource_status_label.setStyleSheet(
            "font-family: monospace; "
            "font-size: 13px; "
            "font-weight: 600;"
        )

        resource_layout.addWidget(
            self.resource_status_label
        )

        self.resource_details_label = QLabel(
            "Resource information will update live while "
            "the DayZ service is running."
        )

        self.resource_details_label.setStyleSheet(
            "font-family: monospace;"
        )

        resource_layout.addWidget(
            self.resource_details_label
        )

        layout.addWidget(
            resource_group
        )

        # ============================================================
        # A2S Server Stats
        # ============================================================

        a2s_group = QGroupBox(
            "A2S Server Stats — LIVE"
        )

        a2s_layout = QHBoxLayout(
            a2s_group
        )

        a2s_info_layout = QVBoxLayout()

        self.a2s_status_label = QLabel(
            "Not connected"
        )

        self.a2s_status_label.setStyleSheet(
            "font-size: 14px; font-weight: 600;"
        )

        a2s_info_layout.addWidget(
            self.a2s_status_label
        )

        self.a2s_details_label = QLabel(
            "A2S server information will appear here "
            "after connecting."
        )

        self.a2s_details_label.setWordWrap(True)

        self.a2s_details_label.setStyleSheet(
            "font-family: monospace;"
        )

        a2s_info_layout.addWidget(
            self.a2s_details_label
        )

        a2s_info_layout.addStretch()

        a2s_layout.addLayout(
            a2s_info_layout,
            1,
        )

        graph_layout = QVBoxLayout()

        graph_header = QHBoxLayout()

        graph_title = QLabel(
            "History"
        )

        graph_title.setStyleSheet(
            "font-size: 13px; "
            "font-weight: 600;"
        )

        graph_header.addWidget(
            graph_title
        )

        graph_header.addStretch()

        self.graph_range_buttons = {}

        for minutes in (
            5,
            15,
            30,
            60,
        ):
            button = QPushButton(
                f"{minutes}m"
            )

            button.setCheckable(True)
            button.setFixedWidth(42)

            button.clicked.connect(
                lambda checked=False,
                value=minutes:
                self._set_graph_range(
                    value
                )
            )

            self.graph_range_buttons[
                minutes
            ] = button

            graph_header.addWidget(
                button
            )

        graph_layout.addLayout(
            graph_header
        )

        self.history_graph = (
            LiveHistoryGraph()
        )

        graph_layout.addWidget(
            self.history_graph,
            1,
        )

        a2s_layout.addLayout(
            graph_layout,
            2,
        )

        self.graph_range_buttons[
            5
        ].setChecked(True)

        layout.addWidget(
            a2s_group
        )

        # ============================================================
        # Server log box
        # ============================================================

        log_group = QGroupBox(
            "Server Log — LIVE"
        )

        log_layout = QVBoxLayout(
            log_group
        )

        self.log_output = QPlainTextEdit()

        self.log_output.setReadOnly(True)

        self.log_output.setLineWrapMode(
            QPlainTextEdit.NoWrap
        )

        self.log_output.setStyleSheet(
            "font-family: monospace;"
        )

        self.log_output.setMaximumBlockCount(
            self.MAX_LOG_LINES
        )

        self.log_search_highlighter = (
            LogSearchHighlighter(
                self.log_output.document()
            )
        )

        log_layout.addWidget(
            self.log_output,
            1,
        )

        # ============================================================
        # Log search
        # ============================================================

        search_row = QHBoxLayout()

        search_row.addWidget(
            QLabel("Search:")
        )

        self.log_search_edit = QLineEdit()

        self.log_search_edit.setPlaceholderText(
            "Enter text to highlight..."
        )

        self.log_search_edit.returnPressed.connect(
            self.find_next
        )

        self.log_search_edit.textChanged.connect(
            self._update_log_search
        )

        search_row.addWidget(
            self.log_search_edit,
            1,
        )

        self.find_prev_btn = QPushButton(
            "Previous"
        )

        self.find_prev_btn.clicked.connect(
            self.find_previous
        )

        search_row.addWidget(
            self.find_prev_btn
        )

        self.find_next_btn = QPushButton(
            "Next"
        )

        self.find_next_btn.clicked.connect(
            self.find_next
        )

        search_row.addWidget(
            self.find_next_btn
        )

        self.clear_search_btn = QPushButton(
            "Clear"
        )

        self.clear_search_btn.clicked.connect(
            self.clear_log_search
        )

        search_row.addWidget(
            self.clear_search_btn
        )

        log_layout.addLayout(
            search_row
        )

        layout.addWidget(
            log_group,
            1,
        )

        # ============================================================
        # LIVE polling timer
        # ============================================================

        self.timer = QTimer(self)

        self.timer.setInterval(
            self.LIVE_INTERVAL_MS
        )

        self.timer.timeout.connect(
            self.refresh_status
        )

        # ============================================================
        # Initial display.
        # ============================================================

        self.status_output.setPlainText(
            "Click Connect to open an SSH session and begin "
            "live server monitoring."
        )

        self.log_output.setPlainText(
            "Server log will appear here after connecting."
        )

        self._set_action_buttons_enabled(
            False
        )

    # ================================================================
    # Graph
    # ================================================================

    def _set_graph_range(
        self,
        minutes,
    ):
        for value, button in (
            self.graph_range_buttons.items()
        ):
            button.setChecked(
                value == minutes
            )

        self.history_graph.set_time_window(
            minutes
        )

    def _extract_server_fps(
        self,
        log_text,
    ):
        """
        Look for an explicit FPS value in the DayZ log.
        """

        if not log_text:
            return None

        patterns = (
            r"(?im)\bserver\s+fps\s*[:=]\s*"
            r"(\d+(?:\.\d+)?)",

            r"(?im)\bfps\s*[:=]\s*"
            r"(\d+(?:\.\d+)?)",
        )

        for pattern in patterns:
            matches = re.findall(
                pattern,
                log_text,
            )

            if matches:
                try:
                    return float(
                        matches[-1]
                    )
                except ValueError:
                    pass

        return None

    def _update_history_graph(
        self,
        log_text="",
    ):
        if self.history_graph is None:
            return

        players = None
        max_players = None

        if self.a2s_stats:
            info = self.a2s_stats.get(
                "info"
            )

            if info is not None:
                players = getattr(
                    info,
                    "player_count",
                    None,
                )

                max_players = getattr(
                    info,
                    "max_players",
                    None,
                )

        cpu = None
        ram = None

        cpu_count = (
            self.remote_cpu_count
            or 1
        )

        if self.resource_stats:
            if self.resource_stats.get(
                "ok"
            ):
                cpu = self.resource_stats.get(
                    "cpu"
                )

                ram = self.resource_stats.get(
                    "memory_percent"
                )

        fps = self._extract_server_fps(
            log_text
        )

        self.history_graph.add_sample(
            time.time(),
            players=players,
            max_players=max_players,
            cpu_percent=cpu,
            cpu_count=cpu_count,
            ram_percent=ram,
            fps=fps,
        )

    # ================================================================
    # Connect / Disconnect
    # ================================================================

    def toggle_connection(self):
        if self.ssh.is_connected():
            self.disconnect_ssh()
        else:
            self.connect_ssh()

    def connect_ssh(self):
        if not self.config.is_configured():
            QMessageBox.information(
                self,
                "Not configured",
                "Fill in SSH host, username, and key path "
                "on the Settings tab first.",
            )
            return

        self.connect_btn.setEnabled(False)

        self.server_state = "unknown"
        self.refresh_running = False
        self.a2s_request_running = False
        self.remote_cpu_count = 1

        self.history_graph.clear_history()

        self.status_label.setText(
            "Connecting..."
        )

        self.live_label.setText(
            "● CONNECTING"
        )

        self.live_label.setStyleSheet(
            "font-size: 13px; "
            "font-weight: 600; "
            "color: #FF9800;"
        )

        self.lamp.set_state("amber")

        def task():
            self.ssh.connect()
            return True

        self.jobs.start(
            task,
            on_ok=self._on_connected,
            on_fail=self._on_connect_failed,
        )

    def _on_connected(self, _result):
        self.connect_btn.setEnabled(True)

        self.connect_btn.setText(
            "Disconnect"
        )

        self.server_state = "unknown"

        self._set_action_buttons_enabled(
            True
        )

        self.live_label.setText(
            "● LIVE"
        )

        self.live_label.setStyleSheet(
            "font-size: 13px; "
            "font-weight: 600; "
            "color: #4CAF50;"
        )

        self.timer.start(
            self.LIVE_INTERVAL_MS
        )

        self.refresh_status()

        if self.on_connection_changed:
            self.on_connection_changed(True)

    def _on_connect_failed(self, error):
        self.connect_btn.setEnabled(True)

        self.server_state = "unknown"
        self.refresh_running = False
        self.a2s_request_running = False

        self.history_graph.clear_history()

        self.status_label.setText(
            "Connection failed"
        )

        self.live_label.setText(
            "● OFFLINE"
        )

        self.live_label.setStyleSheet(
            "font-size: 13px; "
            "font-weight: 600; "
            "color: #F44336;"
        )

        self.lamp.set_state("red")

        self.status_output.setPlainText(
            str(error)
        )

        self.log_output.setPlainText(
            "Unable to connect to the server."
        )

        self._set_action_buttons_enabled(False)

        self._clear_a2s_display(
            "Not connected"
        )

        self._clear_resource_display()

    def disconnect_ssh(self):
        self.timer.stop()

        self.refresh_running = False
        self.a2s_request_running = False

        self.ssh.close()

        self.server_state = "unknown"

        self.history_graph.clear_history()

        self.remote_cpu_count = 1

        self.connect_btn.setText(
            "Connect"
        )

        self.status_label.setText(
            "Not connected"
        )

        self.live_label.setText(
            "● OFFLINE"
        )

        self.live_label.setStyleSheet(
            "font-size: 13px; "
            "font-weight: 600; "
            "color: #9E9E9E;"
        )

        self.update_label.setText(
            "Last update: --"
        )

        self.lamp.set_state("off")

        self.status_output.setPlainText(
            "Click Connect to open an SSH session and begin "
            "live server monitoring."
        )

        self.log_output.setPlainText(
            "Server log will appear here after connecting."
        )

        self._set_action_buttons_enabled(False)

        self._clear_a2s_display(
            "Not connected"
        )

        self._clear_resource_display()

        if self.on_connection_changed:
            self.on_connection_changed(False)

    # ================================================================
    # Button state
    # ================================================================

    def _set_action_buttons_enabled(
        self,
        enabled,
    ):
        for button in (
            self.start_btn,
            self.restart_btn,
            self.stop_btn,
            self.enable_btn,
            self.disable_btn,
            self.refresh_btn,
        ):
            button.setEnabled(enabled)

        self.log_search_edit.setEnabled(
            enabled
        )

        self.find_prev_btn.setEnabled(
            enabled
        )

        self.find_next_btn.setEnabled(
            enabled
        )

        self.clear_search_btn.setEnabled(
            enabled
        )

    # ================================================================
    # Generic SSH runner
    # ================================================================

    def _run(self, fn, on_ok):
        if not self.ssh.is_connected():
            QMessageBox.information(
                self,
                "Not connected",
                "Click Connect first.",
            )
            return

        self._set_action_buttons_enabled(False)

        def success(result):
            on_ok(result)

            if self.ssh.is_connected():
                self._set_action_buttons_enabled(True)

                self.refresh_status()

        def failure(error):
            self._on_error(error)

            if self.ssh.is_connected():
                self._set_action_buttons_enabled(True)

        self.jobs.start(
            fn,
            on_ok=success,
            on_fail=failure,
        )

    def _on_error(self, err):
        self.server_state = "unknown"

        self.status_label.setText(
            "Error"
        )

        self.live_label.setText(
            "● MONITOR ERROR"
        )

        self.live_label.setStyleSheet(
            "font-size: 13px; "
            "font-weight: 600; "
            "color: #F44336;"
        )

        self.lamp.set_state("red")

        self.status_output.setPlainText(
            str(err)
        )

        if not self.log_output.toPlainText().strip():
            self.log_output.setPlainText(
                str(err)
            )

    # ================================================================
    # LIVE STATUS POLLING
    # ================================================================

    def refresh_status(self):
        """
        Perform one complete live monitoring cycle.

        The Qt GUI thread is never blocked. All SSH commands and the
        A2S UDP query run inside the WorkerRegistry worker.
        """

        if not self.ssh.is_connected():
            return

        if self.refresh_running:
            return

        self.refresh_running = True

        def task():
            service_name = self.config.systemd_service

            status_result = self.ssh.service_status(
                service_name
            )

            active_command = (
                "systemctl is-active "
                + self._shell_quote(service_name)
                + " 2>/dev/null || true"
            )

            active_code, active_out, active_err = (
                self.ssh.exec(active_command)
            )

            active_lines = (
                (active_out or "")
                .strip()
                .splitlines()
            )

            if active_lines:
                active_state = (
                    active_lines[0]
                    .strip()
                    .lower()
                )
            else:
                active_state = "unknown"

            log_command = (
                "journalctl -q -u "
                + self._shell_quote(service_name)
                + " -n "
                + str(self.MAX_LOG_LINES)
                + " --no-pager"
            )

            log_result = self.ssh.exec(
                log_command
            )

            resource_result = self._fetch_resource_stats(
                service_name
            )

            now = time.monotonic()

            if (
                not self.a2s_request_running
                and (
                    now - self.last_a2s_time
                    >= self.A2S_INTERVAL_MS / 1000.0
                )
            ):
                self.a2s_request_running = True
                self.last_a2s_time = now

                a2s_result = self._fetch_a2s_stats()

            else:
                a2s_result = None

            return (
                status_result,
                log_result,
                active_state,
                resource_result,
                a2s_result,
            )

        def success(result):
            try:
                self._on_status_result(result)

                self.last_refresh_time = time.monotonic()

                self.update_label.setText(
                    "Last update: now"
                )

                self.live_label.setText(
                    "● LIVE"
                )

                self.live_label.setStyleSheet(
                    "font-size: 13px; "
                    "font-weight: 600; "
                    "color: #4CAF50;"
                )

            finally:
                self.refresh_running = False

        def failure(error):
            self.refresh_running = False
            self.a2s_request_running = False

            self._on_error(error)

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    # ================================================================
    # LIVE RESOURCE MONITOR
    # ================================================================

    def _fetch_resource_stats(
        self,
        service_name,
    ):
        pid_command = (
            "systemctl show "
            + self._shell_quote(service_name)
            + " -p MainPID --no-pager"
        )

        pid_code, pid_out, pid_err = self.ssh.exec(
            pid_command
        )

        if pid_code != 0:
            return {
                "ok": False,
                "error": (
                    pid_err.strip()
                    or pid_out.strip()
                    or "Could not determine MainPID."
                ),
            }

        pid_match = re.search(
            r"MainPID=(\d+)",
            pid_out or "",
        )

        if not pid_match:
            return {
                "ok": False,
                "error": "MainPID was not returned.",
            }

        try:
            pid = int(pid_match.group(1))
        except ValueError:
            return {
                "ok": False,
                "error": "Invalid MainPID.",
            }

        if pid <= 0:
            return {
                "ok": False,
                "pid": 0,
                "error": "Service is not currently running.",
            }

        ps_command = (
            "ps -p "
            + str(pid)
            + " -o pid=,%cpu=,%mem=,rss=,etime=,comm="
        )

        ps_code, ps_out, ps_err = self.ssh.exec(
            ps_command
        )

        if ps_code != 0:
            return {
                "ok": False,
                "pid": pid,
                "error": (
                    ps_err.strip()
                    or ps_out.strip()
                    or "Could not read process statistics."
                ),
            }

        line = ""

        for candidate in (ps_out or "").splitlines():
            if candidate.strip():
                line = candidate.strip()
                break

        if not line:
            return {
                "ok": False,
                "pid": pid,
                "error": "Process statistics were empty.",
            }

        parts = line.split(None, 5)

        if len(parts) < 6:
            return {
                "ok": False,
                "pid": pid,
                "error": (
                    "Could not parse process statistics."
                ),
            }

        try:
            parsed_pid = int(parts[0])
            cpu = float(parts[1])
            memory_percent = float(parts[2])
            rss_kib = int(parts[3])
            elapsed = parts[4]
            command = parts[5]
        except (ValueError, IndexError):
            return {
                "ok": False,
                "pid": pid,
                "error": (
                    "Invalid process statistics returned by ps."
                ),
            }

        if self.remote_cpu_count <= 1:
            cpu_count_command = (
                "getconf _NPROCESSORS_ONLN "
                "2>/dev/null "
                "|| nproc 2>/dev/null "
                "|| echo 1"
            )

            cpu_count_code, cpu_count_out, cpu_count_err = (
                self.ssh.exec(
                    cpu_count_command
                )
            )

            if cpu_count_code == 0:
                try:
                    detected_cpu_count = int(
                        cpu_count_out.strip().splitlines()[0]
                    )

                    if detected_cpu_count > 0:
                        self.remote_cpu_count = (
                            detected_cpu_count
                        )

                except (
                    ValueError,
                    IndexError,
                ):
                    pass

        return {
            "ok": True,
            "pid": parsed_pid,
            "cpu": cpu,
            "memory_percent": memory_percent,
            "rss_kib": rss_kib,
            "elapsed": elapsed,
            "command": command,
            "cpu_count": (
                self.remote_cpu_count
                or 1
            ),
        }

    def _display_resource_stats(
        self,
        result,
    ):
        if not result:
            self._clear_resource_display()
            return

        if not result.get("ok"):
            pid = result.get("pid")

            if pid:
                self.resource_status_label.setText(
                    f"PID: {pid}    Resource data unavailable"
                )
            else:
                self.resource_status_label.setText(
                    "CPU: --    RAM: --    PID: --    Uptime: --"
                )

            self.resource_details_label.setText(
                str(
                    result.get(
                        "error",
                        "Resource information unavailable.",
                    )
                )
            )

            self.resource_stats = result

            return

        pid = result.get(
            "pid",
            0,
        )

        cpu = result.get(
            "cpu",
            0.0,
        )

        memory_percent = result.get(
            "memory_percent",
            0.0,
        )

        rss_kib = result.get(
            "rss_kib",
            0,
        )

        elapsed = result.get(
            "elapsed",
            "--",
        )

        command = result.get(
            "command",
            "",
        )

        rss_mb = rss_kib / 1024.0

        self.resource_status_label.setText(
            f"CPU: {cpu:.1f}%    "
            f"RAM: {memory_percent:.1f}%    "
            f"PID: {pid}    "
            f"Uptime: {elapsed}"
        )

        self.resource_details_label.setText(
            f"Process: {command}    "
            f"RSS: {rss_mb:.1f} MB"
        )

        self.resource_stats = result

    def _clear_resource_display(self):
        self.resource_stats = None

        self.resource_status_label.setText(
            "CPU: --    RAM: --    PID: --    Uptime: --"
        )

        self.resource_details_label.setText(
            "Resource information will update live while "
            "the DayZ service is running."
        )

    # ================================================================
    # A2S SERVER STATS
    # ================================================================

    def _fetch_a2s_stats(self):
        service_name = self.config.systemd_service

        inspect_command = (
            "systemctl show "
            + self._shell_quote(service_name)
            + " -p ExecStart -p WorkingDirectory --no-pager"
        )

        inspect_code, inspect_out, inspect_err = (
            self.ssh.exec(inspect_command)
        )

        if inspect_code != 0:
            return self._a2s_query_with_fallback(
                "Could not inspect systemd service; "
                "using default query port 27016."
            )

        exec_start = ""
        working_directory = ""

        for line in inspect_out.splitlines():
            if line.startswith("ExecStart="):
                exec_start = (
                    line[len("ExecStart="):]
                    .strip()
                )

            elif line.startswith("WorkingDirectory="):
                working_directory = (
                    line[len("WorkingDirectory="):]
                    .strip()
                )

        config_path = self._extract_config_path(
            exec_start
        )

        if not config_path:
            if working_directory:
                config_path = (
                    working_directory.rstrip("/")
                    + "/serverDZ.cfg"
                )
            else:
                server_root = str(
                    getattr(
                        self.config,
                        "server_root",
                        "",
                    )
                    or ""
                ).strip().rstrip("/")

                if server_root:
                    config_path = (
                        server_root
                        + "/serverDZ.cfg"
                    )

        if not config_path:
            return self._a2s_query_with_fallback(
                "serverDZ.cfg path could not be determined; "
                "using default query port 27016."
            )

        if not config_path.startswith("/"):
            if working_directory:
                config_path = (
                    working_directory.rstrip("/")
                    + "/"
                    + config_path.lstrip("/")
                )
            else:
                server_root = str(
                    getattr(
                        self.config,
                        "server_root",
                        "",
                    )
                    or ""
                ).strip().rstrip("/")

                if server_root:
                    config_path = (
                        server_root
                        + "/"
                        + config_path.lstrip("/")
                    )

        read_command = (
            "cat "
            + self._shell_quote(config_path)
        )

        read_code, config_text, read_err = (
            self.ssh.exec(read_command)
        )

        if read_code != 0:
            return self._a2s_query_with_fallback(
                "Could not read serverDZ.cfg; "
                "using default query port 27016."
            )

        query_match = re.search(
            r"(?m)^[ \t]*"
            r"(?<!//)"
            r"steamQueryPort"
            r"[ \t]*=[ \t]*"
            r"([0-9]+)"
            r"[ \t]*;",
            config_text or "",
        )

        if query_match:
            try:
                query_port = int(
                    query_match.group(1)
                )

                if not (
                    1 <= query_port <= 65535
                ):
                    raise ValueError

            except ValueError:
                query_port = 27016
                query_source = "default"
            else:
                query_source = "serverDZ.cfg"

        else:
            query_port = 27016
            query_source = "default"

        host = str(
            self.config.host
        ).strip()

        if not host:
            return {
                "ok": False,
                "error": (
                    "SSH host is empty; "
                    "cannot perform A2S query."
                ),
                "query_port": query_port,
                "query_source": query_source,
                "config_path": config_path,
            }

        try:
            info = a2s.info(
                (
                    host,
                    query_port,
                ),
                timeout=3.0,
            )

        except Exception as error:
            return {
                "ok": False,
                "error": str(error),
                "query_port": query_port,
                "query_source": query_source,
                "config_path": config_path,
            }

        return {
            "ok": True,
            "info": info,
            "query_port": query_port,
            "query_source": query_source,
            "config_path": config_path,
        }

    def _a2s_query_with_fallback(
        self,
        reason,
    ):
        query_port = 27016

        host = str(
            self.config.host
        ).strip()

        if not host:
            return {
                "ok": False,
                "error": (
                    reason
                    + "\n\nSSH host is empty."
                ),
                "query_port": query_port,
                "query_source": "default",
                "config_path": "",
            }

        try:
            info = a2s.info(
                (
                    host,
                    query_port,
                ),
                timeout=3.0,
            )

        except Exception as error:
            return {
                "ok": False,
                "error": (
                    reason
                    + "\n\n"
                    + str(error)
                ),
                "query_port": query_port,
                "query_source": "default",
                "config_path": "",
            }

        return {
            "ok": True,
            "info": info,
            "query_port": query_port,
            "query_source": "default",
            "config_path": "",
        }

    @staticmethod
    def _extract_config_path(
        exec_start,
    ):
        if not exec_start:
            return ""

        match = re.search(
            r"(?:^|\s)-config="
            r"(\"[^\"]*\"|'[^']*'|\S+)",
            exec_start,
        )

        if not match:
            return ""

        value = match.group(1).strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in (
                "'",
                '"',
            )
        ):
            value = value[1:-1]

        return value

    def _on_a2s_result(self, result):
        if result is None:
            return

        self.a2s_request_running = False

        if not result.get("ok"):
            self._show_a2s_error(result)
            return

        info = result.get("info")

        if info is None:
            self._clear_a2s_display(
                "Unavailable"
            )
            return

        self.a2s_stats = result

        self._display_a2s_info(result)

    def _display_a2s_info(
        self,
        result,
    ):
        info = result.get("info")

        query_port = result.get(
            "query_port",
            27016,
        )

        query_source = result.get(
            "query_source",
            "default",
        )

        server_name = str(
            getattr(
                info,
                "server_name",
                "",
            )
            or ""
        ).strip()

        map_name = str(
            getattr(
                info,
                "map_name",
                "",
            )
            or ""
        ).strip()

        player_count = getattr(
            info,
            "player_count",
            None,
        )

        max_players = getattr(
            info,
            "max_players",
            None,
        )

        version = str(
            getattr(
                info,
                "version",
                "",
            )
            or ""
        ).strip()

        vac_enabled = getattr(
            info,
            "vac_enabled",
            None,
        )

        self.a2s_status_label.setText(
            "ONLINE"
        )

        self.a2s_status_label.setStyleSheet(
            "font-size: 14px; "
            "font-weight: 600; "
            "color: #4CAF50;"
        )

        if (
            player_count is not None
            and max_players is not None
        ):
            players_text = (
                f"{player_count} / {max_players}"
            )

        elif player_count is not None:
            players_text = str(player_count)

        else:
            players_text = "Unknown"

        host = str(
            self.config.host
        ).strip()

        if host and getattr(info, "port", None):
            address = (
                f"{host}:{getattr(info, 'port')}"
            )

        elif host:
            address = host

        else:
            address = "Unknown"

        if query_source == "serverDZ.cfg":
            query_text = (
                f"{query_port} "
                "(serverDZ.cfg)"
            )
        else:
            query_text = (
                f"{query_port} "
                "(default)"
            )

        if vac_enabled is True:
            vac_text = "Enabled"

        elif vac_enabled is False:
            vac_text = "Disabled"

        else:
            vac_text = "Unknown"

        lines = []

        if server_name:
            lines.append(
                f"Server:     {server_name}"
            )

        lines.append(
            f"Players:    {players_text}"
        )

        if map_name:
            lines.append(
                f"Map:        {map_name}"
            )

        lines.append(
            f"Address:    {address}"
        )

        lines.append(
            f"Query Port: {query_text}"
        )

        if version:
            lines.append(
                f"Version:    {version}"
            )

        lines.append(
            f"VAC:        {vac_text}"
        )

        config_path = result.get(
            "config_path",
            "",
        )

        if config_path:
            lines.append(
                f"Config:     {config_path}"
            )

        self.a2s_details_label.setText(
            "\n".join(lines)
        )

    def _show_a2s_error(
        self,
        result,
    ):
        self.a2s_request_running = False
        self.a2s_stats = None

        query_port = result.get(
            "query_port",
            27016,
        )

        query_source = result.get(
            "query_source",
            "default",
        )

        if query_source == "serverDZ.cfg":
            query_text = (
                f"{query_port} "
                "(serverDZ.cfg)"
            )
        else:
            query_text = (
                f"{query_port} "
                "(default)"
            )

        self.a2s_status_label.setText(
            "UNREACHABLE"
        )

        self.a2s_status_label.setStyleSheet(
            "font-size: 14px; "
            "font-weight: 600; "
            "color: #F44336;"
        )

        error_text = str(
            result.get(
                "error",
                "No A2S response.",
            )
        ).strip()

        self.a2s_details_label.setText(
            "A2S query failed.\n\n"
            f"Query Port: {query_text}\n"
            f"Reason: {error_text}"
        )

    def _clear_a2s_display(
        self,
        status_text,
    ):
        self.a2s_request_running = False
        self.a2s_stats = None

        self.a2s_status_label.setText(
            status_text
        )

        self.a2s_status_label.setStyleSheet(
            "font-size: 14px; font-weight: 600;"
        )

        if status_text == "Not connected":
            self.a2s_details_label.setText(
                "A2S server information will be refreshed "
                "after reconnecting."
            )
        else:
            self.a2s_details_label.setText(
                "No A2S server information is available."
            )

    # ================================================================
    # LIVE RESULT HANDLING
    # ================================================================

    def _on_status_result(
        self,
        result,
    ):
        (
            status_result,
            log_result,
            active_state,
            resource_result,
            a2s_result,
        ) = result

        self._on_a2s_result(
            a2s_result
        )

        self._display_resource_stats(
            resource_result
        )

        code, out, err = status_result

        status_lines = []

        for line in (out or "").splitlines():
            if re.match(
                r"^[A-Z][a-z]{2}\s+\d{2}\s+\d{2}:\d{2}:\d{2}\s+",
                line,
            ):
                continue

            status_lines.append(line)

        status_text = "\n".join(
            status_lines
        ).rstrip()

        if err:
            if status_text:
                status_text += (
                    "\n\n"
                    + err.strip()
                )
            else:
                status_text = err.strip()

        self.status_output.setPlainText(
            status_text
        )

        log_code, log_out, log_err = log_result

        log_text = log_out or ""

        if log_err:
            if log_text:
                log_text += (
                    "\n\n"
                    + log_err
                )
            else:
                log_text = log_err

        log_lines = log_text.splitlines()

        filtered_log_lines = []

        skip_journal_hint = False

        for line in log_lines:
            stripped = line.strip()
            lowered = stripped.lower()

            if lowered.startswith(
                "hint: you are currently not seeing messages"
            ):
                skip_journal_hint = True
                continue

            if skip_journal_hint:
                if (
                    "users in groups" in lowered
                    or "pass -q to turn off this notice"
                    in lowered
                ):
                    continue

                if not stripped:
                    continue

                skip_journal_hint = False

            if (
                "users in groups 'adm', 'systemd-journal', 'wheel'"
                in lowered
            ):
                continue

            if (
                "pass -q to turn off this notice"
                in lowered
            ):
                continue

            filtered_log_lines.append(line)

        log_text = "\n".join(
            filtered_log_lines
        ).rstrip()

        if not log_text.strip():
            log_text = (
                "No journal output was returned for "
                f"{self.config.systemd_service}."
            )

        scrollbar = (
            self.log_output.verticalScrollBar()
        )

        saved_scroll_value = scrollbar.value()

        saved_cursor = (
            self.log_output.textCursor()
        )

        saved_cursor_position = (
            saved_cursor.position()
        )

        saved_cursor_anchor = (
            saved_cursor.anchor()
        )

        search_active = (
            self.log_search_active
        )

        self.log_output.setPlainText(
            log_text
        )

        self.log_search_highlighter.rehighlight()

        if search_active:

            def restore_log_view():
                scrollbar = (
                    self.log_output.verticalScrollBar()
                )

                max_value = (
                    scrollbar.maximum()
                )

                if max_value <= 0:
                    scrollbar.setValue(0)
                else:
                    scrollbar.setValue(
                        min(
                            saved_scroll_value,
                            max_value,
                        )
                    )

                cursor = (
                    self.log_output.textCursor()
                )

                character_count = (
                    self.log_output.document()
                    .characterCount()
                )

                position = max(
                    0,
                    min(
                        saved_cursor_position,
                        character_count - 1,
                    ),
                )

                anchor = max(
                    0,
                    min(
                        saved_cursor_anchor,
                        character_count - 1,
                    ),
                )

                cursor.setPosition(
                    anchor
                )

                cursor.setPosition(
                    position,
                    QTextCursor.KeepAnchor,
                )

                self.log_output.setTextCursor(
                    cursor
                )

            QTimer.singleShot(
                0,
                restore_log_view,
            )

        else:

            def scroll_log_to_bottom():
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

            QTimer.singleShot(
                0,
                scroll_log_to_bottom,
            )

        self._update_history_graph(
            log_text
        )

        state = (
            active_state or "unknown"
        ).strip().lower()

        if state == "active":
            self.server_state = "active"

            if re.search(
                r"Active:\s+active\s+\(running\)",
                out or "",
                re.IGNORECASE,
            ):
                self.status_label.setText(
                    "Running"
                )
            else:
                self.status_label.setText(
                    "Active"
                )

            self.lamp.set_state("green")

        elif state == "inactive":
            self.server_state = "inactive"

            self.status_label.setText(
                "Stopped"
            )

            self.lamp.set_state("red")

        elif state == "failed":
            self.server_state = "failed"

            self.status_label.setText(
                "Failed"
            )

            self.lamp.set_state("red")

        elif state == "activating":
            self.server_state = "activating"

            self.status_label.setText(
                "Starting..."
            )

            self.lamp.set_state("amber")

        elif state == "deactivating":
            self.server_state = "deactivating"

            self.status_label.setText(
                "Stopping..."
            )

            self.lamp.set_state("amber")

        else:
            self.server_state = "unknown"

            self.status_label.setText(
                "Unknown"
            )

            self.lamp.set_state("amber")

    # ================================================================
    # Log search
    # ================================================================

    def _update_log_search(
        self,
        text,
    ):
        self.log_search_active = bool(
            text.strip()
        )

        self.log_search_highlighter.set_search_text(
            text
        )

        if text:
            self._find_match(
                text,
                forward=True,
                wrap=True,
            )

    def clear_log_search(self):
        self.log_search_edit.clear()

        self.log_search_active = False

        self.log_search_highlighter.set_search_text(
            ""
        )

        QTimer.singleShot(
            0,
            self._scroll_log_to_bottom,
        )

    def _scroll_log_to_bottom(self):
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

    def find_next(self):
        text = self.log_search_edit.text()

        if not text:
            return

        self._find_match(
            text,
            forward=True,
            wrap=True,
        )

    def find_previous(self):
        text = self.log_search_edit.text()

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
        document = self.log_output.document()

        cursor = self.log_output.textCursor()

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

            if (
                found_cursor.isNull()
                and wrap
            ):
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

            if (
                found_cursor.isNull()
                and wrap
            ):
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

    # ================================================================
    # Service actions
    # ================================================================

    def run_action(
        self,
        action,
    ):
        if action in (
            "stop",
            "restart",
        ):
            response = QMessageBox.question(
                self,
                "Confirm",
                f"Really {action} the DayZ server?",
            )

            if response != QMessageBox.Yes:
                return

        elif action == "disable":
            response = QMessageBox.question(
                self,
                "Confirm",
                "Disable automatic startup for "
                f"{self.config.systemd_service}?\n\n"
                "This runs systemctl disable and will not stop "
                "a currently running server.",
            )

            if response != QMessageBox.Yes:
                return

        elif action == "enable":
            response = QMessageBox.question(
                self,
                "Confirm",
                "Enable automatic startup for "
                f"{self.config.systemd_service}?",
            )

            if response != QMessageBox.Yes:
                return

        password = (
            self.sudo_password_edit.text()
        )

        def task():
            return self.ssh.service_action(
                self.config.systemd_service,
                action,
                sudo_password=password or None,
            )

        self._run(
            task,
            self._on_action_result,
        )

    def _on_action_result(
        self,
        result,
    ):
        code, out, err = result

        if code != 0:
            hint = ""

            error_text = (
                err or ""
            ).lower()

            if (
                "incorrect password"
                in error_text
                or "sorry, try again"
                in error_text
            ):
                hint = (
                    "\n\nThe sudo password looks wrong - "
                    "check the field above and try again."
                )

            elif (
                "a password is required"
                in error_text
                or "no tty present"
                in error_text
            ):
                hint = (
                    "\n\nEnter your sudo password in the field "
                    "above and try again."
                )

            QMessageBox.warning(
                self,
                "Command failed",
                (
                    (err or "").strip()
                    or (out or "").strip()
                    or "Unknown error"
                )
                + hint,
            )

        self.refresh_running = False

        if self.ssh.is_connected():
            self.refresh_status()

    # ================================================================
    # Helpers
    # ================================================================

    @staticmethod
    def _shell_quote(value):
        """Quote a string for a POSIX shell command."""

        value = str(value)

        return (
            "'"
            + value.replace(
                "'",
                "'\\''",
            )
            + "'"
        )
