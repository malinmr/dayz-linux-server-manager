import re
import shlex
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from worker import WorkerRegistry
import steam_web_api


DAYZ_APP_ID = "221100"


class ModTypeDialog(QDialog):
    def __init__(
        self,
        mod_name="",
        mod_type="mod",
        enabled=True,
        parent=None,
    ):
        super().__init__(parent)

        self.setWindowTitle("Edit Mod")
        self.resize(400, 180)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(mod_name)

        self.type_edit = QComboBox()
        self.type_edit.addItem("Mod", "mod")
        self.type_edit.addItem("Server Mod", "servermod")

        normalized_type = str(
            mod_type or "mod"
        ).strip().lower()

        if normalized_type == "servermod":
            self.type_edit.setCurrentIndex(1)
        else:
            self.type_edit.setCurrentIndex(0)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(enabled)

        form.addRow("Name:", self.name_edit)
        form.addRow("Type:", self.type_edit)
        form.addRow("", self.enabled_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
            | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def values(self):
        mod_type = self.type_edit.currentData()

        if mod_type not in (
            "mod",
            "servermod",
        ):
            mod_type = "mod"

        return (
            self.name_edit.text().strip(),
            mod_type,
            self.enabled_check.isChecked(),
        )


class ModTableWidget(QTableWidget):
    """
    Custom mouse-based drag/reorder table.
    """

    reorder_requested = Signal(int, int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._pressed_row = -1
        self._press_pos = None
        self._dragging = False

        self._drop_row = -1
        self._drop_after = False

        self.setMouseTracking(True)

        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(
            QAbstractItemView.NoDragDrop
        )

        self.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()

            self._pressed_row = self.rowAt(
                int(pos.y())
            )

            self._press_pos = pos
            self._dragging = False

            self._drop_row = -1
            self._drop_after = False

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._pressed_row >= 0
            and self._press_pos is not None
            and event.buttons() & Qt.LeftButton
        ):
            pos = event.position().toPoint()

            distance = (
                pos - self._press_pos
            ).manhattanLength()

            if (
                not self._dragging
                and distance
                >= QApplication.startDragDistance()
            ):
                self._dragging = True

                self.selectRow(
                    self._pressed_row
                )

                self.setCursor(
                    Qt.ClosedHandCursor
                )

            if self._dragging:
                self._update_drop_indicator(
                    int(pos.y())
                )

                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self._dragging
        ):
            source_row = self._pressed_row

            if (
                source_row >= 0
                and source_row < self.rowCount()
            ):
                pos = event.position().toPoint()

                destination_row = (
                    self._calculate_destination(
                        int(pos.y()),
                        source_row,
                    )
                )

                self._clear_drag_state()

                if (
                    destination_row >= 0
                    and destination_row != source_row
                ):
                    self.reorder_requested.emit(
                        source_row,
                        destination_row,
                    )

                event.accept()
                return

        self._clear_drag_state()

        super().mouseReleaseEvent(event)

    def _update_drop_indicator(self, y):
        if self.rowCount() <= 0:
            self._drop_row = -1
            self._drop_after = False
            self.viewport().update()
            return

        target_row = self.rowAt(y)

        if target_row < 0:
            target_row = self.rowCount() - 1
            drop_after = True
        else:
            item = self.item(
                target_row,
                0,
            )

            if item is None:
                drop_after = False
            else:
                rect = self.visualItemRect(
                    item
                )

                drop_after = (
                    y > rect.center().y()
                )

        self._drop_row = target_row
        self._drop_after = drop_after

        self.viewport().update()

    def _calculate_destination(
        self,
        y,
        source_row,
    ):
        if self.rowCount() <= 1:
            return source_row

        target_row = self.rowAt(y)

        if target_row < 0:
            destination = (
                self.rowCount() - 1
            )
        else:
            item = self.item(
                target_row,
                0,
            )

            if item is None:
                destination = target_row
            else:
                rect = self.visualItemRect(
                    item
                )

                if y > rect.center().y():
                    destination = (
                        target_row + 1
                    )
                else:
                    destination = target_row

        if destination > source_row:
            destination -= 1

        return max(
            0,
            min(
                destination,
                self.rowCount() - 1,
            ),
        )

    def _clear_drag_state(self):
        self._pressed_row = -1
        self._press_pos = None
        self._dragging = False

        self._drop_row = -1
        self._drop_after = False

        self.setCursor(
            Qt.ArrowCursor
        )

        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)

        if not self._dragging:
            return

        if self._drop_row < 0:
            return

        if self.rowCount() <= 0:
            return

        item = self.item(
            self._drop_row,
            0,
        )

        if item is None:
            return

        rect = self.visualItemRect(
            item
        )

        if self._drop_after:
            y = rect.bottom() + 1
        else:
            y = rect.top()

        painter = QPainter(
            self.viewport()
        )

        try:
            pen = QPen(
                QColor("#2196F3")
            )

            pen.setWidth(3)

            painter.setPen(pen)

            left = 2
            right = (
                self.viewport().width() - 2
            )

            painter.drawLine(
                left,
                y,
                right,
                y,
            )

            painter.setBrush(
                QColor("#2196F3")
            )

            painter.setPen(
                Qt.NoPen
            )

            painter.drawEllipse(
                left - 3,
                y - 4,
                8,
                8,
            )

            painter.drawEllipse(
                right - 5,
                y - 4,
                8,
                8,
            )

        finally:
            painter.end()


class ModsPanel(QWidget):
    steamcmd_output = Signal(str)

    mod_parameters_generated = Signal(str, str)

    def __init__(
        self,
        ssh,
        config,
        parent=None,
    ):
        super().__init__(parent)

        self.ssh = ssh
        self.config = config
        self.jobs = WorkerRegistry()

        self._connected = False

        self.steamcmd_output.connect(
            self._append_log
        )

        self._migrate_mods()

        self._build_ui()

        self.set_connected(
            self.ssh.is_connected()
        )

    # ==============================================================
    # Configuration
    # ==============================================================

    def _migrate_mods(self):
        changed = False

        if not isinstance(
            self.config.mods,
            list,
        ):
            self.config.mods = []
            changed = True

        cleaned_mods = []

        for mod in self.config.mods:
            if not isinstance(
                mod,
                dict,
            ):
                changed = True
                continue

            if "id" in mod:
                mod["id"] = str(
                    mod["id"]
                ).strip()

            if "type" not in mod:
                mod["type"] = "mod"
                changed = True

            if "enabled" not in mod:
                mod["enabled"] = True
                changed = True

            if "status" not in mod:
                mod["status"] = "installed"
                changed = True

            if "update_status" not in mod:
                mod["update_status"] = "unknown"
                changed = True

            if "local_workshop_time" not in mod:
                mod["local_workshop_time"] = None
                changed = True

            if "remote_workshop_time" not in mod:
                mod["remote_workshop_time"] = None
                changed = True

            cleaned_mods.append(mod)

        if len(cleaned_mods) != len(
            self.config.mods
        ):
            self.config.mods = cleaned_mods
            changed = True

        if changed:
            self.config.save()

    # ==============================================================
    # UI
    # ==============================================================

    def _build_ui(self):
        root = QVBoxLayout(self)

        # --------------------------------------------------------------
        # Workshop Search
        # --------------------------------------------------------------

        search_group = QGroupBox(
            "Workshop Search"
        )

        search_layout = QVBoxLayout(
            search_group
        )

        search_row = QHBoxLayout()

        self.search_edit = QLineEdit()

        self.search_edit.setPlaceholderText(
            "Search Steam Workshop..."
        )

        self.search_edit.returnPressed.connect(
            self.search_workshop
        )

        self.search_button = QPushButton(
            "Search"
        )

        self.search_button.clicked.connect(
            self.search_workshop
        )

        search_row.addWidget(
            self.search_edit
        )

        search_row.addWidget(
            self.search_button
        )

        search_layout.addLayout(
            search_row
        )

        self.search_results = QTableWidget(
            0,
            3,
        )

        self.search_results.setHorizontalHeaderLabels(
            [
                "Title",
                "Workshop ID",
                "Subscribers",
            ]
        )

        self.search_results.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )

        self.search_results.setSelectionMode(
            QAbstractItemView.SingleSelection
        )

        self.search_results.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.Stretch,
        )

        self.search_results.itemDoubleClicked.connect(
            lambda _item: self.use_selected_workshop()
        )

        search_layout.addWidget(
            self.search_results
        )

        use_row = QHBoxLayout()

        self.use_selected_button = QPushButton(
            "Use Selected ↓"
        )

        self.use_selected_button.clicked.connect(
            self.use_selected_workshop
        )

        use_row.addWidget(
            self.use_selected_button
        )

        use_row.addStretch()

        search_layout.addLayout(
            use_row
        )

        root.addWidget(
            search_group
        )

        # --------------------------------------------------------------
        # Add / Install
        # --------------------------------------------------------------

        add_group = QGroupBox(
            "Add / Install Workshop Mod"
        )

        add_layout = QVBoxLayout(
            add_group
        )

        id_row = QHBoxLayout()

        self.id_edit = QLineEdit()

        self.id_edit.setPlaceholderText(
            "Workshop ID"
        )

        self.name_edit = QLineEdit()

        self.name_edit.setPlaceholderText(
            "Mod name"
        )

        self.fetch_name_button = QPushButton(
            "Fetch Name from ID"
        )

        self.fetch_name_button.clicked.connect(
            self.fetch_name
        )

        self.download_button = QPushButton(
            "Download + Install Mod"
        )

        self.download_button.clicked.connect(
            self.download_and_install
        )

        id_row.addWidget(
            QLabel("Workshop ID:")
        )

        id_row.addWidget(
            self.id_edit
        )

        id_row.addWidget(
            QLabel("Name:")
        )

        id_row.addWidget(
            self.name_edit
        )

        id_row.addWidget(
            self.fetch_name_button
        )

        id_row.addWidget(
            self.download_button
        )

        add_layout.addLayout(
            id_row
        )

        root.addWidget(
            add_group
        )

        # --------------------------------------------------------------
        # Installed Mods
        # --------------------------------------------------------------

        installed_group = QGroupBox(
            "Installed Mods / Priority"
        )

        installed_layout = QVBoxLayout(
            installed_group
        )

        priority_label = QLabel(
            "Drag rows up or down to change priority. "
            "Priority 1 is the first row. "
            "The order below is used for -mod= and -servermod=."
        )

        priority_label.setWordWrap(
            True
        )

        installed_layout.addWidget(
            priority_label
        )

        self.installed_table = ModTableWidget(
            0,
            7,
        )

        self.installed_table.setHorizontalHeaderLabels(
            [
                "ID",
                "Name",
                "Status",
                "Type",
                "Enabled",
                "Key",
                "Symlink",
            ]
        )

        self.installed_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )

        self.installed_table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )

        header = (
            self.installed_table.horizontalHeader()
        )

        header.setSectionResizeMode(
            QHeaderView.Interactive
        )

        header.setStretchLastSection(
            False
        )

        saved_widths = getattr(
            self.config,
            "mods_column_widths",
            [
                150,
                260,
                130,
                100,
                80,
                70,
                90,
            ],
        )

        default_widths = [
            150,
            260,
            130,
            100,
            80,
            70,
            90,
        ]

        for column in range(
            self.installed_table.columnCount()
        ):
            if (
                isinstance(
                    saved_widths,
                    list,
                )
                and column < len(
                    saved_widths
                )
            ):
                try:
                    width = int(
                        saved_widths[column]
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    width = default_widths[
                        column
                    ]
            else:
                width = default_widths[
                    column
                ]

            self.installed_table.setColumnWidth(
                column,
                max(
                    40,
                    width,
                ),
            )

        header.sectionResized.connect(
            self._mods_column_resized
        )

        self.installed_table.reorder_requested.connect(
            self._mods_reordered
        )

        installed_layout.addWidget(
            self.installed_table
        )

        button_row = QHBoxLayout()

        self.check_updates_button = QPushButton(
            "Check Mod Updates"
        )

        self.check_updates_button.clicked.connect(
            self.check_mod_updates
        )

        self.resync_keys_button = QPushButton(
            "Re-sync Keys for Selected"
        )

        self.resync_keys_button.clicked.connect(
            self.resync_keys_selected
        )

        self.edit_button = QPushButton(
            "Edit Selected"
        )

        self.edit_button.clicked.connect(
            self.edit_selected
        )

        self.remove_button = QPushButton(
            "Remove Selected from List"
        )

        self.remove_button.clicked.connect(
            self.remove_selected
        )

        self.write_button = QPushButton(
            "Send Mod List to Systemd Panel"
        )

        self.write_button.clicked.connect(
            self.write_mod_list
        )

        button_row.addWidget(
            self.check_updates_button
        )

        button_row.addWidget(
            self.resync_keys_button
        )

        button_row.addWidget(
            self.edit_button
        )

        button_row.addWidget(
            self.remove_button
        )

        button_row.addWidget(
            self.write_button
        )

        installed_layout.addLayout(
            button_row
        )

        root.addWidget(
            installed_group
        )

        # --------------------------------------------------------------
        # Log
        # --------------------------------------------------------------

        log_group = QGroupBox(
            "Log"
        )

        log_layout = QVBoxLayout(
            log_group
        )

        self.log_box = QPlainTextEdit()

        self.log_box.setReadOnly(
            True
        )

        log_layout.addWidget(
            self.log_box
        )

        root.addWidget(
            log_group
        )

    # ==============================================================
    # Column persistence
    # ==============================================================

    def _mods_column_resized(
        self,
        logical_index,
        old_size,
        new_size,
    ):
        widths = [
            self.installed_table.columnWidth(
                column
            )
            for column in range(
                self.installed_table.columnCount()
            )
        ]

        self.config.mods_column_widths = widths
        self.config.save()

    # ==============================================================
    # Connection
    # ==============================================================

    def set_connected(self, connected):
        connected = bool(
            connected
        )

        was_connected = self._connected

        self._connected = connected

        self.search_edit.setEnabled(
            True
        )

        self.search_button.setEnabled(
            True
        )

        self.search_results.setEnabled(
            True
        )

        self.use_selected_button.setEnabled(
            True
        )

        self.id_edit.setEnabled(
            True
        )

        self.name_edit.setEnabled(
            True
        )

        self.fetch_name_button.setEnabled(
            True
        )

        self.installed_table.setEnabled(
            True
        )

        self.edit_button.setEnabled(
            True
        )

        self.remove_button.setEnabled(
            True
        )

        self.download_button.setEnabled(
            connected
        )

        self.resync_keys_button.setEnabled(
            connected
        )

        self.check_updates_button.setEnabled(
            connected
        )

        self.write_button.setEnabled(
            True
        )

        if connected and not was_connected:
            self._append_log(
                "SSH connection established. "
                "Refreshing mod Key/Symlink status..."
            )

            self._refresh_installed_table()

            self._append_log(
                "Checking Workshop mod update status..."
            )

            self.check_mod_updates(
                silent=True
            )

    # ==============================================================
    # Logging
    # ==============================================================

    def _append_log(self, text):
        if not text:
            return

        self.log_box.appendPlainText(
            str(text)
        )

        scrollbar = (
            self.log_box.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )

    # ==============================================================
    # Steam Workshop Search
    # ==============================================================

    def search_workshop(self):
        query = (
            self.search_edit.text().strip()
        )

        if not query:
            self._append_log(
                "Enter a Workshop search term."
            )
            return

        api_key = (
            self.config.steam_api_key.strip()
        )

        if not api_key:
            QMessageBox.information(
                self,
                "No Steam API Key",
                "Set a Steam Web API key on the Settings tab first.",
            )
            return

        self._append_log(
            f"Workshop search requested: {query}"
        )

        self.search_button.setEnabled(
            False
        )

        def task():
            return steam_web_api.search_workshop(
                api_key,
                query,
            )

        def success(results):
            self.search_button.setEnabled(
                True
            )

            self._populate_search_results(
                results
            )

        def failure(error):
            self.search_button.setEnabled(
                True
            )

            self._append_log(
                f"Workshop search failed: {error}"
            )

            QMessageBox.warning(
                self,
                "Workshop Search Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    def _populate_search_results(
        self,
        results,
    ):
        self.search_results.setRowCount(
            0
        )

        if not results:
            self._append_log(
                "No Workshop results found."
            )
            return

        for item in results:
            if not isinstance(
                item,
                dict,
            ):
                continue

            row = (
                self.search_results.rowCount()
            )

            self.search_results.insertRow(
                row
            )

            title = str(
                item.get(
                    "title",
                    "",
                )
            )

            workshop_id = str(
                item.get(
                    "id",
                    "",
                )
            )

            subscriptions = str(
                item.get(
                    "subscriptions",
                    0,
                )
            )

            self.search_results.setItem(
                row,
                0,
                QTableWidgetItem(
                    title
                ),
            )

            self.search_results.setItem(
                row,
                1,
                QTableWidgetItem(
                    workshop_id
                ),
            )

            self.search_results.setItem(
                row,
                2,
                QTableWidgetItem(
                    subscriptions
                ),
            )

        self._append_log(
            f"Workshop search returned "
            f"{self.search_results.rowCount()} "
            f"result(s)."
        )

    def use_selected_workshop(self):
        row = (
            self.search_results.currentRow()
        )

        if row < 0:
            QMessageBox.information(
                self,
                "No Selection",
                "Select a Workshop search result first.",
            )
            return

        id_item = (
            self.search_results.item(
                row,
                1,
            )
        )

        if id_item is None:
            return

        workshop_id = (
            id_item.text().strip()
        )

        name_item = (
            self.search_results.item(
                row,
                0,
            )
        )

        name = (
            name_item.text().strip()
            if name_item is not None
            else ""
        )

        folder_name = (
            re.sub(
                r"[^A-Za-z0-9_]+",
                "_",
                name,
            )
            .strip("_")
            or f"mod{workshop_id}"
        )

        self.id_edit.setText(
            workshop_id
        )

        self.name_edit.setText(
            folder_name
        )

        self._append_log(
            f"Selected Workshop item "
            f"{name} ({workshop_id})."
        )

    def fetch_name(self):
        workshop_id = (
            self.id_edit.text().strip()
        )

        if not workshop_id.isdigit():
            QMessageBox.information(
                self,
                "Missing ID",
                "Enter a numeric Workshop ID first.",
            )
            return

        api_key = (
            self.config.steam_api_key.strip()
        )

        if not api_key:
            QMessageBox.information(
                self,
                "No Steam API Key",
                "Set a Steam Web API key on the Settings tab first.",
            )
            return

        self._append_log(
            f"Fetching Workshop name for "
            f"{workshop_id}..."
        )

        self.fetch_name_button.setEnabled(
            False
        )

        def task():
            return steam_web_api.get_details(
                api_key,
                workshop_id,
            )

        def success(details):
            self.fetch_name_button.setEnabled(
                True
            )

            self._on_workshop_info_fetched(
                details
            )

        def failure(error):
            self.fetch_name_button.setEnabled(
                True
            )

            self._append_log(
                f"Workshop lookup failed: {error}"
            )

            QMessageBox.warning(
                self,
                "Workshop Lookup Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    def _on_workshop_info_fetched(
        self,
        details,
    ):
        if not details:
            QMessageBox.information(
                self,
                "Not Found",
                "No Workshop item was found for that ID.",
            )
            return

        title = str(
            details.get(
                "title",
                "",
            )
        ).strip()

        workshop_id = str(
            details.get(
                "id",
                self.id_edit.text().strip(),
            )
        ).strip()

        folder_name = (
            re.sub(
                r"[^A-Za-z0-9_]+",
                "_",
                title,
            )
            .strip("_")
            or f"mod{workshop_id}"
        )

        self.name_edit.setText(
            folder_name
        )

        self._append_log(
            f"Workshop item found: "
            f"{title} ({workshop_id})"
        )

        self._append_log(
            f"Suggested folder name: "
            f"{folder_name}"
        )

    # ==============================================================
    # Remote paths
    # ==============================================================

    def _workshop_path(self, workshop_id):
        return (
            f"{self.config.server_root.rstrip('/')}"
            f"/steamapps/workshop/content/{DAYZ_APP_ID}"
            f"/{workshop_id}"
        )

    def _server_link_path(self, workshop_id):
        return (
            f"{self.config.server_root.rstrip('/')}"
            f"/{workshop_id}"
        )

    def _workshop_manifest_path(self):
        return (
            f"{self.config.server_root.rstrip('/')}"
            f"/steamapps/workshop/"
            f"appworkshop_{DAYZ_APP_ID}.acf"
        )

    def _find_remote_workshop_item(
        self,
        workshop_id,
    ):
        path = self._workshop_path(
            workshop_id
        )

        code, out, err = (
            self.ssh.exec(
                "test -d "
                + shlex.quote(
                    path
                )
            )
        )

        if code == 0:
            return path

        return None

    # ==============================================================
    # Workshop Update Check
    # ==============================================================

    def _extract_keyvalues_block(
        self,
        content,
        key,
    ):
        """
        Extract a complete KeyValues block for the specified key.

        Steam ACF files can contain nested braces, so simply searching
        until the next '}' is not reliable.
        """

        pattern = re.compile(
            rf'"{re.escape(str(key))}"\s*\{{',
            re.MULTILINE,
        )

        match = pattern.search(
            content
        )

        if not match:
            return None

        opening_brace = content.find(
            "{",
            match.start(),
        )

        if opening_brace < 0:
            return None

        depth = 0
        in_quotes = False
        escaped = False

        for index in range(
            opening_brace,
            len(content),
        ):
            char = content[index]

            if in_quotes:
                if escaped:
                    escaped = False

                elif char == "\\":
                    escaped = True

                elif char == '"':
                    in_quotes = False

                continue

            if char == '"':
                in_quotes = True

            elif char == "{":
                depth += 1

            elif char == "}":
                depth -= 1

                if depth == 0:
                    return content[
                        opening_brace + 1:index
                    ]

        return None

    def _extract_all_workshop_item_blocks(
        self,
        content,
        workshop_id,
    ):
        """
        Find every ACF block for the requested Workshop ID.

        The same Workshop ID may appear in multiple sections of the
        appworkshop_221100.acf file.
        """

        workshop_id = str(
            workshop_id
        ).strip()

        if not workshop_id:
            return []

        pattern = re.compile(
            rf'"{re.escape(workshop_id)}"\s*\{{',
            re.MULTILINE,
        )

        blocks = []

        for match in pattern.finditer(
            content
        ):
            opening_brace = content.find(
                "{",
                match.start(),
            )

            if opening_brace < 0:
                continue

            depth = 0
            in_quotes = False
            escaped = False

            for index in range(
                opening_brace,
                len(content),
            ):
                char = content[index]

                if in_quotes:
                    if escaped:
                        escaped = False

                    elif char == "\\":
                        escaped = True

                    elif char == '"':
                        in_quotes = False

                    continue

                if char == '"':
                    in_quotes = True

                elif char == "{":
                    depth += 1

                elif char == "}":
                    depth -= 1

                    if depth == 0:
                        blocks.append(
                            content[
                                opening_brace + 1:index
                            ]
                        )

                        break

        return blocks

    def _extract_workshop_item_block(
        self,
        content,
        workshop_id,
    ):
        """
        Return the Workshop ID block containing timeupdated.

        Steam's ACF may contain the same Workshop ID multiple times.
        The old implementation only checked the first occurrence,
        which caused some older installed mods to show UNKNOWN.
        """

        blocks = (
            self._extract_all_workshop_item_blocks(
                content,
                workshop_id,
            )
        )

        if not blocks:
            return None

        # Best case: a block containing timeupdated.
        for block in blocks:
            if re.search(
                r'"timeupdated"\s+"?([0-9]+)"?',
                block,
                re.IGNORECASE,
            ):
                return block

        # Second choice: a block containing a manifest.
        for block in blocks:
            if re.search(
                r'"manifest"\s+"?([0-9]+)"?',
                block,
                re.IGNORECASE,
            ):
                return block

        return blocks[0]

    def _read_local_workshop_metadata(
        self,
        workshop_id,
    ):
        """
        Read local Steam Workshop metadata from:

            steamapps/workshop/appworkshop_221100.acf
        """

        manifest_path = (
            self._workshop_manifest_path()
        )

        command = (
            "if [ -f "
            + shlex.quote(
                manifest_path
            )
            + " ]; then cat "
            + shlex.quote(
                manifest_path
            )
            + "; else exit 2; fi"
        )

        code, output, error = (
            self.ssh.exec(
                command
            )
        )

        if code == 2:
            self._append_worker_output(
                f"Workshop manifest not found: "
                f"{manifest_path}"
            )

            return None

        if code != 0:
            raise RuntimeError(
                "Failed to read local Workshop manifest:\n"
                f"{error}"
            )

        if not output.strip():
            self._append_worker_output(
                "Workshop manifest is empty."
            )

            return None

        block = (
            self._extract_workshop_item_block(
                output,
                workshop_id,
            )
        )

        if not block:
            self._append_worker_output(
                f"No ACF entry found for Workshop "
                f"ID {workshop_id}."
            )

            return None

        time_match = re.search(
            r'"timeupdated"\s+"?([0-9]+)"?',
            block,
            re.IGNORECASE,
        )

        manifest_match = re.search(
            r'"manifest"\s+"?([0-9]+)"?',
            block,
            re.IGNORECASE,
        )

        local_time = None

        if time_match:
            try:
                local_time = int(
                    time_match.group(1)
                )
            except (
                TypeError,
                ValueError,
            ):
                local_time = None

        local_manifest = None

        if manifest_match:
            local_manifest = (
                manifest_match.group(1)
            )

        if local_time is None:
            self._append_worker_output(
                f"Workshop ID {workshop_id}: "
                "ACF entry found, but no "
                "timeupdated value was present."
            )
        else:
            self._append_worker_output(
                f"Workshop ID {workshop_id}: "
                f"local timeupdated = {local_time}"
            )

        return {
            "timeupdated": local_time,
            "manifest": local_manifest,
        }

    def _get_remote_workshop_update_time(
        self,
        workshop_id,
        api_key,
    ):
        """
        Get the current Steam Workshop update timestamp.
        """

        details = steam_web_api.get_details(
            api_key,
            workshop_id,
        )

        if not details:
            self._append_worker_output(
                f"{workshop_id}: Steam Workshop "
                "item details were not returned."
            )

            return None

        possible_values = (
            details.get(
                "time_updated"
            ),
            details.get(
                "timeupdated"
            ),
            details.get(
                "timeUpdated"
            ),
        )

        for value in possible_values:
            if value is None:
                continue

            try:
                return int(
                    value
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

        self._append_worker_output(
            f"{workshop_id}: Steam Workshop details "
            "did not contain a valid time_updated value."
        )

        return None

    def _check_single_mod_update(
        self,
        mod,
        api_key,
    ):
        workshop_id = str(
            mod.get(
                "id",
                "",
            )
        ).strip()

        if not workshop_id:
            return {
                "status": "unknown",
                "local_time": None,
                "remote_time": None,
            }

        # ----------------------------------------------------------
        # Check the actual Workshop content directory.
        # ----------------------------------------------------------

        workshop_path = (
            self._find_remote_workshop_item(
                workshop_id
            )
        )

        if not workshop_path:
            self._append_worker_output(
                f"{workshop_id}: Workshop content "
                "directory does not exist."
            )

            return {
                "status": "unknown",
                "local_time": None,
                "remote_time": None,
            }

        # ----------------------------------------------------------
        # Read local Workshop metadata.
        # ----------------------------------------------------------

        local_metadata = (
            self._read_local_workshop_metadata(
                workshop_id
            )
        )

        if not local_metadata:
            self._append_worker_output(
                f"{workshop_id}: Could not find local "
                "Workshop metadata."
            )

            return {
                "status": "unknown",
                "local_time": None,
                "remote_time": None,
            }

        local_time = (
            local_metadata.get(
                "timeupdated"
            )
        )

        # ----------------------------------------------------------
        # Get current Steam Workshop timestamp.
        # ----------------------------------------------------------

        remote_time = (
            self._get_remote_workshop_update_time(
                workshop_id,
                api_key,
            )
        )

        if remote_time is None:
            return {
                "status": "unknown",
                "local_time": local_time,
                "remote_time": None,
            }

        if local_time is None:
            self._append_worker_output(
                f"{workshop_id}: Local Workshop metadata "
                "has no usable timeupdated value."
            )

            return {
                "status": "unknown",
                "local_time": None,
                "remote_time": remote_time,
            }

        # ----------------------------------------------------------
        # Compare timestamps.
        # ----------------------------------------------------------

        self._append_worker_output(
            f"{workshop_id}: "
            f"local={local_time}, "
            f"remote={remote_time}"
        )

        if remote_time > local_time:
            status = "needs-update"

            self._append_worker_output(
                f"{workshop_id}: NEED UPDATE"
            )

        else:
            status = "up-to-date"

            self._append_worker_output(
                f"{workshop_id}: UP TO DATE"
            )

        return {
            "status": status,
            "local_time": local_time,
            "remote_time": remote_time,
        }

    def check_mod_updates(
        self,
        silent=False,
    ):
        if not self.ssh.is_connected():
            if not silent:
                QMessageBox.warning(
                    self,
                    "Not Connected",
                    "Connect to the DayZ server first.",
                )
            return

        api_key = (
            self.config.steam_api_key.strip()
        )

        if not api_key:
            if not silent:
                QMessageBox.information(
                    self,
                    "No Steam API Key",
                    "Set a Steam Web API key on the Settings tab first.",
                )
            return

        mods = [
            mod
            for mod in self.config.mods
            if isinstance(
                mod,
                dict,
            )
        ]

        if not mods:
            if not silent:
                QMessageBox.information(
                    self,
                    "No Mods",
                    "There are no mods in the manager.",
                )
            return

        self.check_updates_button.setEnabled(
            False
        )

        # Show that a fresh check is running.
        for mod in mods:
            mod["update_status"] = "checking"

        self._refresh_installed_table()

        self._append_log(
            "--- Checking Workshop mod updates ---"
        )

        def task():
            results = []

            for index, mod in enumerate(
                mods
            ):
                workshop_id = str(
                    mod.get(
                        "id",
                        "",
                    )
                ).strip()

                name = str(
                    mod.get(
                        "name",
                        workshop_id,
                    )
                ).strip()

                if not workshop_id:
                    continue

                self._append_worker_output(
                    f"Checking {name} "
                    f"({workshop_id})..."
                )

                result = (
                    self._check_single_mod_update(
                        mod,
                        api_key,
                    )
                )

                result["index"] = index
                result["workshop_id"] = workshop_id

                results.append(
                    result
                )

            return results

        def success(results):
            try:
                up_to_date = 0
                needs_update = 0
                unknown = 0

                for result in results:
                    index = result.get(
                        "index",
                        -1,
                    )

                    if (
                        index < 0
                        or index >= len(
                            self.config.mods
                        )
                    ):
                        continue

                    mod = self.config.mods[
                        index
                    ]

                    status = result.get(
                        "status",
                        "unknown",
                    )

                    mod["update_status"] = (
                        status
                    )

                    mod["local_workshop_time"] = (
                        result.get(
                            "local_time"
                        )
                    )

                    mod["remote_workshop_time"] = (
                        result.get(
                            "remote_time"
                        )
                    )

                    workshop_id = str(
                        mod.get(
                            "id",
                            "",
                        )
                    ).strip()

                    name = str(
                        mod.get(
                            "name",
                            workshop_id,
                        )
                    ).strip()

                    if status == "up-to-date":
                        up_to_date += 1

                        self._append_log(
                            f"UP TO DATE: "
                            f"{name} ({workshop_id})"
                        )

                    elif status == "needs-update":
                        needs_update += 1

                        self._append_log(
                            f"NEED UPDATE: "
                            f"{name} ({workshop_id})"
                        )

                    else:
                        unknown += 1

                        self._append_log(
                            f"UPDATE STATUS UNKNOWN: "
                            f"{name} ({workshop_id})"
                        )

                self.config.save()

                self._refresh_installed_table()

                self._append_log(
                    "--- Mod update check finished ---"
                )

                self._append_log(
                    f"Up to date: {up_to_date}"
                )

                self._append_log(
                    f"Need update: {needs_update}"
                )

                if unknown:
                    self._append_log(
                        f"Unknown: {unknown}"
                    )

                if (
                    not silent
                    and needs_update > 0
                ):
                    QMessageBox.information(
                        self,
                        "Mod Updates",
                        (
                            f"{needs_update} mod(s) "
                            "need an update."
                        ),
                    )

            finally:
                self.check_updates_button.setEnabled(
                    self._connected
                )

        def failure(error):
            self.check_updates_button.setEnabled(
                self._connected
            )

            # Don't leave rows stuck on CHECKING.
            for mod in self.config.mods:
                if (
                    mod.get("update_status")
                    == "checking"
                ):
                    mod["update_status"] = "unknown"

            self._refresh_installed_table()

            self._append_log(
                f"ERROR checking mod updates: "
                f"{error}"
            )

            if not silent:
                QMessageBox.warning(
                    self,
                    "Mod Update Check Failed",
                    str(error),
                )

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    def _set_update_status_item(
        self,
        item,
        status,
    ):
        status = str(
            status
            or "unknown"
        ).strip().lower()

        if status == "up-to-date":
            item.setText(
                "UP TO DATE"
            )

            item.setForeground(
                QColor("#4CAF50")
            )

        elif status == "needs-update":
            item.setText(
                "NEED UPDATE"
            )

            item.setForeground(
                QColor("#FF4D4D")
            )

        elif status == "checking":
            item.setText(
                "CHECKING..."
            )

            item.setForeground(
                QColor("#2196F3")
            )

        else:
            item.setText(
                "UNKNOWN"
            )

            item.setForeground(
                QColor("#FF9800")
            )

    # ==============================================================
    # Key discovery
    # ==============================================================

    def _get_mod_key_files(
        self,
        workshop_id,
    ):
        workshop_path = (
            self._find_remote_workshop_item(
                workshop_id
            )
        )

        if not workshop_path:
            return []

        command = (
            "find "
            + shlex.quote(
                workshop_path
            )
            + " -type f "
            r"\( "
            "-iname '*.bikey' "
            r"\)"
        )

        code, out, err = (
            self.ssh.exec(
                command
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Failed to scan keys for "
                f"{workshop_id}:\n{err}"
            )

        return [
            line.strip()
            for line in out.splitlines()
            if line.strip()
        ]

    def _get_mod_key_names(
        self,
        workshop_id,
    ):
        return {
            path.rsplit(
                "/",
                1,
            )[-1]
            for path in self._get_mod_key_files(
                workshop_id
            )
        }

    # ==============================================================
    # Shared key protection
    # ==============================================================

    def _other_mods_using_key(
        self,
        key_name,
        excluded_workshop_id=None,
    ):
        users = []

        for mod in self.config.mods:
            if not isinstance(
                mod,
                dict,
            ):
                continue

            workshop_id = str(
                mod.get(
                    "id",
                    "",
                )
            ).strip()

            if not workshop_id:
                continue

            if (
                excluded_workshop_id
                and workshop_id
                == str(
                    excluded_workshop_id
                ).strip()
            ):
                continue

            if not bool(
                mod.get(
                    "enabled",
                    True,
                )
            ):
                continue

            try:
                key_names = (
                    self._get_mod_key_names(
                        workshop_id
                    )
                )
            except Exception as exc:
                self._append_log(
                    f"WARNING: Could not inspect keys for "
                    f"{workshop_id}: {exc}"
                )

                users.append(
                    workshop_id
                )
                continue

            if key_name in key_names:
                users.append(
                    workshop_id
                )

        return users

    def _remove_unused_keys(
        self,
        workshop_id,
        key_files,
    ):
        keys_dir = (
            self.config.keys_dir.strip()
        )

        if not keys_dir:
            return

        for key_file in key_files:
            key_name = key_file.rsplit(
                "/",
                1,
            )[-1]

            users = (
                self._other_mods_using_key(
                    key_name,
                    excluded_workshop_id=workshop_id,
                )
            )

            destination = (
                f"{keys_dir.rstrip('/')}"
                f"/{key_name}"
            )

            if users:
                self._append_worker_output(
                    f"Keeping shared key {key_name}; "
                    f"still used by: "
                    f"{', '.join(users)}"
                )
                continue

            code, out, err = (
                self.ssh.exec(
                    "if [ -L "
                    + shlex.quote(
                        destination
                    )
                    + " ]; then rm -f "
                    + shlex.quote(
                        destination
                    )
                    + "; fi"
                )
            )

            if code != 0:
                raise RuntimeError(
                    f"Failed to remove key link "
                    f"{destination}:\n{err}"
                )

            self._append_worker_output(
                f"Removed unused key link: "
                f"{destination}"
            )

    # ==============================================================
    # Symlink
    # ==============================================================

    def _remove_mod_symlink(
        self,
        workshop_id,
    ):
        server_link = (
            self._server_link_path(
                workshop_id
            )
        )

        code, out, err = (
            self.ssh.exec(
                "if [ -L "
                + shlex.quote(
                    server_link
                )
                + " ]; then rm -f "
                + shlex.quote(
                    server_link
                )
                + "; fi"
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Failed to remove mod symlink "
                f"{server_link}:\n{err}"
            )

        self._append_worker_output(
            f"Removed mod symlink: "
            f"{server_link}"
        )

    def _create_mod_symlink(
        self,
        workshop_id,
    ):
        workshop_path = (
            self._find_remote_workshop_item(
                workshop_id
            )
        )

        if not workshop_path:
            raise RuntimeError(
                f"Workshop content for "
                f"{workshop_id} was not found."
            )

        server_link = (
            self._server_link_path(
                workshop_id
            )
        )

        code, out, err = (
            self.ssh.exec(
                "if [ -L "
                + shlex.quote(
                    server_link
                )
                + " ]; then rm -f "
                + shlex.quote(
                    server_link
                )
                + "; fi"
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Failed to clear existing symlink "
                f"{server_link}:\n{err}"
            )

        code, out, err = (
            self.ssh.exec(
                "if [ -e "
                + shlex.quote(
                    server_link
                )
                + " ] && [ ! -L "
                + shlex.quote(
                    server_link
                )
                + " ]; then "
                "echo 'DESTINATION_EXISTS_AS_REAL_FILE_OR_DIRECTORY'; "
                "exit 2; fi"
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Cannot create mod symlink because "
                f"the destination already exists as a real "
                f"file or directory:\n{server_link}"
            )

        code, out, err = (
            self.ssh.exec(
                "ln -s "
                + shlex.quote(
                    workshop_path
                )
                + " "
                + shlex.quote(
                    server_link
                )
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Failed to create mod symlink:\n"
                f"{err}"
            )

        self._append_worker_output(
            f"Created mod symlink: "
            f"{server_link} -> {workshop_path}"
        )

    # ==============================================================
    # Key activation
    # ==============================================================

    def _install_mod_keys(
        self,
        workshop_id,
    ):
        keys_dir = (
            self.config.keys_dir.strip()
        )

        if not keys_dir:
            self._append_worker_output(
                "Keys directory is not configured; "
                "skipping key installation."
            )
            return 0

        key_files = (
            self._get_mod_key_files(
                workshop_id
            )
        )

        if not key_files:
            self._append_worker_output(
                f"No .bikey files found for "
                f"{workshop_id}."
            )
            return 0

        code, out, err = (
            self.ssh.exec(
                "mkdir -p "
                + shlex.quote(
                    keys_dir
                )
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Failed to create keys directory:\n"
                f"{err}"
            )

        linked = 0

        for key_file in key_files:
            key_name = key_file.rsplit(
                "/",
                1,
            )[-1]

            destination = (
                f"{keys_dir.rstrip('/')}"
                f"/{key_name}"
            )

            users = (
                self._other_mods_using_key(
                    key_name,
                    excluded_workshop_id=workshop_id,
                )
            )

            if users:
                code, out, err = (
                    self.ssh.exec(
                        "test -e "
                        + shlex.quote(
                            destination
                        )
                        + " || test -L "
                        + shlex.quote(
                            destination
                        )
                    )
                )

                if code == 0:
                    self._append_worker_output(
                        f"Key already active and shared: "
                        f"{key_name}"
                    )
                    continue

            code, out, err = (
                self.ssh.exec(
                    "if [ -L "
                    + shlex.quote(
                        destination
                    )
                    + " ]; then "
                    "exit 0; "
                    "fi; "
                    "if [ -e "
                    + shlex.quote(
                        destination
                    )
                    + " ]; then "
                    "exit 2; "
                    "fi; "
                    "ln -s "
                    + shlex.quote(
                        key_file
                    )
                    + " "
                    + shlex.quote(
                        destination
                    )
                )
            )

            if code == 2:
                self._append_worker_output(
                    f"WARNING: Key destination exists as "
                    f"a real file, leaving it untouched: "
                    f"{destination}"
                )
                continue

            if code != 0:
                raise RuntimeError(
                    f"Failed to activate key "
                    f"{key_name}:\n{err}"
                )

            linked += 1

            self._append_worker_output(
                f"Activated key: {destination}"
            )

        return linked

    # ==============================================================
    # Remote activation/deactivation
    # ==============================================================

    def _enable_mod_worker(
        self,
        workshop_id,
    ):
        workshop_path = (
            self._find_remote_workshop_item(
                workshop_id
            )
        )

        if not workshop_path:
            raise RuntimeError(
                f"Workshop download not found for "
                f"{workshop_id}.\n\n"
                "The mod cannot be enabled until its "
                "Workshop content has been downloaded."
            )

        self._append_worker_output(
            f"Enabling mod {workshop_id}..."
        )

        self._create_mod_symlink(
            workshop_id
        )

        key_count = (
            self._install_mod_keys(
                workshop_id
            )
        )

        self._append_worker_output(
            f"Mod {workshop_id} enabled. "
            f"Activated {key_count} key link(s)."
        )

        return True

    def _disable_mod_worker(
        self,
        workshop_id,
    ):
        self._append_worker_output(
            f"Disabling mod {workshop_id}..."
        )

        key_files = (
            self._get_mod_key_files(
                workshop_id
            )
        )

        self._remove_mod_symlink(
            workshop_id
        )

        self._remove_unused_keys(
            workshop_id,
            key_files,
        )

        self._append_worker_output(
            f"Mod {workshop_id} disabled. "
            "Workshop download was kept."
        )

        return True

    # ==============================================================
    # Installed table
    # ==============================================================

    def _refresh_installed_table(self):
        table = self.installed_table

        table.blockSignals(
            True
        )

        try:
            table.setRowCount(
                0
            )

            for mod in self.config.mods:
                if not isinstance(
                    mod,
                    dict,
                ):
                    continue

                workshop_id = str(
                    mod.get(
                        "id",
                        "",
                    )
                ).strip()

                name = str(
                    mod.get(
                        "name",
                        workshop_id,
                    )
                ).strip()

                mod_type = str(
                    mod.get(
                        "type",
                        "mod",
                    )
                ).strip().lower()

                enabled = bool(
                    mod.get(
                        "enabled",
                        True,
                    )
                )

                update_status = str(
                    mod.get(
                        "update_status",
                        "unknown",
                    )
                ).strip()

                symlink = "Unknown"
                key = "Unknown"

                if (
                    self.ssh.is_connected()
                    and workshop_id
                ):
                    try:
                        server_link = (
                            self._server_link_path(
                                workshop_id
                            )
                        )

                        code, out, err = (
                            self.ssh.exec(
                                "test -L "
                                + shlex.quote(
                                    server_link
                                )
                            )
                        )

                        symlink = (
                            "Yes"
                            if code == 0
                            else "No"
                        )

                    except Exception as exc:
                        symlink = "Unknown"

                        self._append_log(
                            f"Symlink check failed for "
                            f"{workshop_id}: {exc}"
                        )

                    try:
                        key_names = (
                            self._get_mod_key_names(
                                workshop_id
                            )
                        )

                        if not key_names:
                            key = "No"
                        else:
                            keys_dir = (
                                self.config.keys_dir.strip()
                            )

                            if not keys_dir:
                                key = "Unknown"
                            else:
                                active_count = 0

                                for key_name in key_names:
                                    destination = (
                                        f"{keys_dir.rstrip('/')}"
                                        f"/{key_name}"
                                    )

                                    code, out, err = (
                                        self.ssh.exec(
                                            "test -L "
                                            + shlex.quote(
                                                destination
                                            )
                                        )
                                    )

                                    if code == 0:
                                        active_count += 1

                                key = (
                                    "Yes"
                                    if active_count > 0
                                    else "No"
                                )

                    except Exception as exc:
                        key = "Unknown"

                        self._append_log(
                            f"Key check failed for "
                            f"{workshop_id}: {exc}"
                        )

                row = table.rowCount()

                table.insertRow(
                    row
                )

                values = [
                    workshop_id,
                    name,
                    update_status,
                    (
                        "Server Mod"
                        if mod_type == "servermod"
                        else "Mod"
                    ),
                    (
                        "Yes"
                        if enabled
                        else "No"
                    ),
                    key,
                    symlink,
                ]

                for column, value in enumerate(
                    values
                ):
                    item = QTableWidgetItem(
                        str(value)
                    )

                    item.setFlags(
                        item.flags()
                        & ~Qt.ItemIsEditable
                    )

                    if column == 2:
                        self._set_update_status_item(
                            item,
                            update_status,
                        )

                    table.setItem(
                        row,
                        column,
                        item,
                    )

        finally:
            table.blockSignals(
                False
            )

    # ==============================================================
    # Priority
    # ==============================================================

    def _mods_reordered(
        self,
        source_row,
        destination_row,
    ):
        mods = list(
            self.config.mods
        )

        if not mods:
            return

        if (
            source_row < 0
            or source_row >= len(mods)
        ):
            return

        if destination_row < 0:
            destination_row = 0

        if destination_row >= len(mods):
            destination_row = (
                len(mods) - 1
            )

        if source_row == destination_row:
            return

        moved_mod = mods.pop(
            source_row
        )

        mods.insert(
            destination_row,
            moved_mod,
        )

        self.config.mods = mods

        self.config.save()

        self._refresh_installed_table()

        if (
            0 <= destination_row
            < self.installed_table.rowCount()
        ):
            self.installed_table.selectRow(
                destination_row
            )

        workshop_id = str(
            moved_mod.get(
                "id",
                "",
            )
        ).strip()

        name = str(
            moved_mod.get(
                "name",
                workshop_id,
            )
        ).strip()

        self._append_log(
            f"Priority changed: {name} "
            f"({workshop_id}) moved from "
            f"{source_row + 1} to "
            f"{destination_row + 1}."
        )

    # ==============================================================
    # SteamCMD
    # ==============================================================

    def _run_steamcmd_live(
        self,
        steamcmd_path,
        steam_user,
        server_root,
        workshop_id,
    ):
        self._append_worker_output(
            "Starting SteamCMD..."
        )

        command = (
            f"{shlex.quote(steamcmd_path)} "
            f"+force_install_dir "
            f"{shlex.quote(server_root)} "
            f"+login "
            f"{shlex.quote(steam_user)} "
            f"+workshop_download_item "
            f"{DAYZ_APP_ID} "
            f"{shlex.quote(workshop_id)} "
            f"+quit"
        )

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
            channel.exec_command(
                command
            )

            buffer = ""

            while True:
                if channel.recv_ready():
                    data = channel.recv(
                        4096
                    )

                    if data:
                        text = data.decode(
                            "utf-8",
                            errors="replace",
                        )

                        buffer += text

                        lines = buffer.splitlines(
                            keepends=True
                        )

                        buffer = ""

                        for line in lines:
                            if line.endswith(
                                ("\n", "\r")
                            ):
                                self._append_worker_output(
                                    line.rstrip()
                                )
                            else:
                                buffer += line

                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(
                        4096
                    )

                    if data:
                        text = data.decode(
                            "utf-8",
                            errors="replace",
                        )

                        for line in text.splitlines():
                            self._append_worker_output(
                                line
                            )

                if channel.exit_status_ready():
                    break

                time.sleep(
                    0.05
                )

            if buffer:
                self._append_worker_output(
                    buffer.rstrip()
                )

            exit_code = (
                channel.recv_exit_status()
            )

            self._append_worker_output(
                f"SteamCMD exited with code "
                f"{exit_code}"
            )

            return exit_code

        finally:
            channel.close()

    def _append_worker_output(
        self,
        text,
    ):
        self.steamcmd_output.emit(
            str(text)
        )

    # ==============================================================
    # Download / Install
    # ==============================================================

    def download_and_install(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the DayZ server first.",
            )
            return

        workshop_id = (
            self.id_edit.text().strip()
        )

        name = (
            self.name_edit.text().strip()
        )

        if not workshop_id:
            QMessageBox.warning(
                self,
                "Missing Workshop ID",
                "Enter a Workshop ID first.",
            )
            return

        if not re.fullmatch(
            r"\d+",
            workshop_id,
        ):
            QMessageBox.warning(
                self,
                "Invalid Workshop ID",
                "Workshop ID must contain only numbers.",
            )
            return

        if not self.config.steam_user.strip():
            QMessageBox.warning(
                self,
                "Steam Username Required",
                "Set your Steam username in Settings "
                "before downloading Workshop content.",
            )
            return

        steamcmd_path = (
            self.config.steamcmd_path.strip()
        )

        server_root = (
            self.config.server_root.strip()
        )

        steam_user = (
            self.config.steam_user.strip()
        )

        if not steamcmd_path:
            QMessageBox.warning(
                self,
                "SteamCMD Path Missing",
                "Set the SteamCMD path in Settings.",
            )
            return

        if not server_root:
            QMessageBox.warning(
                self,
                "Server Root Missing",
                "Set the DayZ server root in Settings.",
            )
            return

        if not name:
            name = workshop_id

        self._append_log(
            f"Downloading Workshop item "
            f"{workshop_id}..."
        )

        self.download_button.setEnabled(
            False
        )

        def task():
            return self._install_mod_worker(
                workshop_id,
                name,
                steamcmd_path,
                steam_user,
                server_root,
            )

        def success(result):
            self.download_button.setEnabled(
                self._connected
            )

            if result:
                self._append_log(
                    f"Mod {name} ({workshop_id}) "
                    "installed successfully."
                )

                self._refresh_installed_table()

                # Check the newly installed mod immediately.
                self.check_mod_updates(
                    silent=True
                )

        def failure(error):
            self.download_button.setEnabled(
                self._connected
            )

            self._append_log(
                f"ERROR: {error}"
            )

            QMessageBox.critical(
                self,
                "Mod Installation Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    def _install_mod_worker(
        self,
        workshop_id,
        name,
        steamcmd_path,
        steam_user,
        server_root,
    ):
        exit_code = (
            self._run_steamcmd_live(
                steamcmd_path,
                steam_user,
                server_root,
                workshop_id,
            )
        )

        if exit_code != 0:
            raise RuntimeError(
                f"SteamCMD failed with exit code "
                f"{exit_code}."
            )

        self._append_worker_output(
            "Checking for downloaded Workshop content..."
        )

        workshop_path = (
            self._find_remote_workshop_item(
                workshop_id
            )
        )

        if not workshop_path:
            raise RuntimeError(
                "Workshop content folder was not found.\n\n"
                "SteamCMD may have returned exit code 0 "
                "even though the Workshop download failed."
            )

        self._append_worker_output(
            "Workshop content found:"
        )

        self._append_worker_output(
            workshop_path
        )

        self._create_mod_symlink(
            workshop_id
        )

        key_count = (
            self._install_mod_keys(
                workshop_id
            )
        )

        self._append_worker_output(
            f"Activated {key_count} key link(s)."
        )

        existing = None

        for mod in self.config.mods:
            if str(
                mod.get(
                    "id",
                    "",
                )
            ).strip() == workshop_id:
                existing = mod
                break

        if existing is None:
            self.config.mods.append(
                {
                    "id": workshop_id,
                    "name": name,
                    "type": "mod",
                    "enabled": True,
                    "status": "installed",
                    "update_status": "unknown",
                    "local_workshop_time": None,
                    "remote_workshop_time": None,
                }
            )

            self._append_worker_output(
                "Added new mod to priority list."
            )

        else:
            existing["name"] = name
            existing["status"] = "installed"
            existing["enabled"] = True

            existing["update_status"] = "unknown"
            existing["local_workshop_time"] = None
            existing["remote_workshop_time"] = None

            if "type" not in existing:
                existing["type"] = "mod"

            self._append_worker_output(
                "Existing mod entry updated."
            )

        self.config.save()

        return True

    # ==============================================================
    # Re-sync Keys
    # ==============================================================

    def resync_keys_selected(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the DayZ server first.",
            )
            return

        row = (
            self.installed_table.currentRow()
        )

        if row < 0:
            QMessageBox.information(
                self,
                "No Mod Selected",
                "Select a mod first.",
            )
            return

        if row >= len(
            self.config.mods
        ):
            return

        mod = self.config.mods[row]

        workshop_id = str(
            mod.get(
                "id",
                "",
            )
        ).strip()

        if not workshop_id:
            return

        def task():
            return self._resync_keys_worker(
                workshop_id
            )

        def success(count):
            self._append_log(
                f"Key re-sync complete. "
                f"Linked {count} key file(s)."
            )

            self._refresh_installed_table()

        def failure(error):
            self._append_log(
                f"ERROR: {error}"
            )

            QMessageBox.critical(
                self,
                "Key Re-sync Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    def _resync_keys_worker(
        self,
        workshop_id,
    ):
        if not bool(
            next(
                (
                    mod.get(
                        "enabled",
                        True,
                    )
                    for mod in self.config.mods
                    if str(
                        mod.get(
                            "id",
                            "",
                        )
                    ).strip()
                    == workshop_id
                ),
                True,
            )
        ):
            raise RuntimeError(
                "The selected mod is disabled. "
                "Enable it before re-syncing its keys."
            )

        key_count = (
            self._install_mod_keys(
                workshop_id
            )
        )

        return key_count

    # ==============================================================
    # Edit Selected
    # ==============================================================

    def edit_selected(self):
        row = (
            self.installed_table.currentRow()
        )

        if row < 0:
            QMessageBox.information(
                self,
                "No Mod Selected",
                "Select a mod first.",
            )
            return

        if row >= len(
            self.config.mods
        ):
            return

        mod = self.config.mods[row]

        old_enabled = bool(
            mod.get(
                "enabled",
                True,
            )
        )

        old_type = str(
            mod.get(
                "type",
                "mod",
            )
        ).strip().lower()

        dialog = ModTypeDialog(
            mod_name=str(
                mod.get(
                    "name",
                    mod.get(
                        "id",
                        "",
                    ),
                )
            ),
            mod_type=old_type,
            enabled=old_enabled,
            parent=self,
        )

        if (
            dialog.exec()
            != QDialog.Accepted
        ):
            return

        name, mod_type, new_enabled = (
            dialog.values()
        )

        workshop_id = str(
            mod.get(
                "id",
                "",
            )
        ).strip()

        if not workshop_id:
            return

        if new_enabled == old_enabled:
            mod["name"] = (
                name
                or workshop_id
            )

            mod["type"] = mod_type
            mod["enabled"] = new_enabled

            self.config.save()

            self._refresh_installed_table()

            self.installed_table.selectRow(
                row
            )

            self._append_log(
                f"Updated {mod['name']} "
                f"({workshop_id}): "
                f"{'Server Mod' if mod_type == 'servermod' else 'Mod'}, "
                f"{'enabled' if new_enabled else 'disabled'}."
            )

            return

        if new_enabled:
            if not self.ssh.is_connected():
                QMessageBox.warning(
                    self,
                    "Not Connected",
                    "Connect to the server before enabling a mod.",
                )
                return

            self.edit_button.setEnabled(
                False
            )

            def task():
                return self._enable_mod_worker(
                    workshop_id
                )

            def success(_result):
                mod["name"] = (
                    name
                    or workshop_id
                )

                mod["type"] = mod_type
                mod["enabled"] = True
                mod["status"] = "installed"

                self.config.save()

                self.edit_button.setEnabled(
                    True
                )

                self._refresh_installed_table()

                self.installed_table.selectRow(
                    row
                )

                self._append_log(
                    f"Enabled {mod['name']} "
                    f"({workshop_id}). "
                    "Workshop download was reused."
                )

            def failure(error):
                self.edit_button.setEnabled(
                    True
                )

                self._append_log(
                    f"ERROR enabling {workshop_id}: "
                    f"{error}"
                )

                QMessageBox.critical(
                    self,
                    "Enable Mod Failed",
                    str(error),
                )

            self.jobs.start(
                task,
                on_ok=success,
                on_fail=failure,
            )

            return

        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the server before disabling a mod.",
            )
            return

        self.edit_button.setEnabled(
            False
        )

        def task():
            return self._disable_mod_worker(
                workshop_id
            )

        def success(_result):
            mod["name"] = (
                name
                or workshop_id
            )

            mod["type"] = mod_type
            mod["enabled"] = False
            mod["status"] = "disabled"

            self.config.save()

            self.edit_button.setEnabled(
                True
            )

            self._refresh_installed_table()

            self.installed_table.selectRow(
                row
            )

            self._append_log(
                f"Disabled {mod['name']} "
                f"({workshop_id}). "
                "Symlink removed; unused keys removed; "
                "Workshop download kept."
            )

        def failure(error):
            self.edit_button.setEnabled(
                True
            )

            self._append_log(
                f"ERROR disabling {workshop_id}: "
                f"{error}"
            )

            QMessageBox.critical(
                self,
                "Disable Mod Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    # ==============================================================
    # Remove Selected Mod
    # ==============================================================

    def remove_selected(self):
        row = (
            self.installed_table.currentRow()
        )

        if row < 0:
            QMessageBox.information(
                self,
                "No Mod Selected",
                "Select a mod first.",
            )
            return

        if row >= len(
            self.config.mods
        ):
            return

        mod = self.config.mods[row]

        workshop_id = str(
            mod.get(
                "id",
                "",
            )
        ).strip()

        name = str(
            mod.get(
                "name",
                workshop_id,
            )
        ).strip()

        if not workshop_id:
            QMessageBox.warning(
                self,
                "Invalid Mod",
                "The selected mod has no Workshop ID.",
            )
            return

        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the DayZ server before removing a mod.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Remove Mod Completely",
            f"Remove {name} ({workshop_id}) completely?\n\n"
            "This will:\n"
            "• Remove the server symlink\n"
            "• Remove unused key links\n"
            "• Delete the Workshop download\n"
            "• Remove the mod from the manager\n\n"
            "Shared keys used by other enabled mods will be kept.",
        )

        if answer != QMessageBox.Yes:
            return

        self.remove_button.setEnabled(
            False
        )

        def task():
            return self._remove_mod_completely_worker(
                workshop_id
            )

        def success(_result):
            self.config.mods.pop(
                row
            )

            self.config.save()

            self.remove_button.setEnabled(
                True
            )

            self._refresh_installed_table()

            self._append_log(
                f"Completely removed {name} "
                f"({workshop_id}). "
                "Workshop download deleted."
            )

        def failure(error):
            self.remove_button.setEnabled(
                True
            )

            self._append_log(
                f"ERROR removing {workshop_id}: "
                f"{error}"
            )

            QMessageBox.critical(
                self,
                "Remove Mod Failed",
                str(error),
            )

        self.jobs.start(
            task,
            on_ok=success,
            on_fail=failure,
        )

    def _remove_mod_completely_worker(
        self,
        workshop_id,
    ):
        self._append_worker_output(
            f"Removing mod {workshop_id}..."
        )

        key_files = (
            self._get_mod_key_files(
                workshop_id
            )
        )

        self._remove_mod_symlink(
            workshop_id
        )

        self._remove_unused_keys(
            workshop_id,
            key_files,
        )

        workshop_path = (
            self._workshop_path(
                workshop_id
            )
        )

        code, out, err = (
            self.ssh.exec(
                "if [ -d "
                + shlex.quote(
                    workshop_path
                )
                + " ]; then rm -rf "
                + shlex.quote(
                    workshop_path
                )
                + "; fi"
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Failed to delete Workshop content:\n"
                f"{err}"
            )

        code, out, err = (
            self.ssh.exec(
                "test ! -e "
                + shlex.quote(
                    workshop_path
                )
            )
        )

        if code != 0:
            raise RuntimeError(
                f"Workshop content still exists after "
                f"removal:\n{workshop_path}"
            )

        self._append_worker_output(
            f"Deleted Workshop download: "
            f"{workshop_path}"
        )

        return True

    # ==============================================================
    # Launch parameter generation
    # ==============================================================

    def _get_launch_mod_lists(self):
        normal_mods = []
        server_mods = []

        for mod in self.config.mods:
            if not isinstance(
                mod,
                dict,
            ):
                continue

            if not bool(
                mod.get(
                    "enabled",
                    True,
                )
            ):
                continue

            workshop_id = str(
                mod.get(
                    "id",
                    "",
                )
            ).strip()

            if not workshop_id:
                continue

            mod_type = str(
                mod.get(
                    "type",
                    "mod",
                )
            ).strip().lower()

            if mod_type == "servermod":
                server_mods.append(
                    workshop_id
                )
            else:
                normal_mods.append(
                    workshop_id
                )

        return (
            normal_mods,
            server_mods,
        )

    # ==============================================================
    # Send mod list to Systemd Panel
    # ==============================================================

    def write_mod_list(self):
        normal_mods, server_mods = (
            self._get_launch_mod_lists()
        )

        normal_value = ";".join(
            normal_mods
        )

        server_value = ";".join(
            server_mods
        )

        if normal_value:
            normal_value += ";"

        if server_value:
            server_value += ";"

        self._append_log(
            "Current mod priority:"
        )

        for index, mod in enumerate(
            self.config.mods,
            start=1,
        ):
            if not isinstance(
                mod,
                dict,
            ):
                continue

            workshop_id = str(
                mod.get(
                    "id",
                    "",
                )
            ).strip()

            name = str(
                mod.get(
                    "name",
                    workshop_id,
                )
            ).strip()

            mod_type = str(
                mod.get(
                    "type",
                    "mod",
                )
            ).strip().lower()

            enabled = bool(
                mod.get(
                    "enabled",
                    True,
                )
            )

            self._append_log(
                f"{index}. {name} "
                f"({workshop_id}) - "
                f"{'Server Mod' if mod_type == 'servermod' else 'Mod'} - "
                f"{'enabled' if enabled else 'disabled'}"
            )

        self._append_log(
            f"-mod={normal_value}"
        )

        self._append_log(
            f"-servermod={server_value}"
        )

        self.mod_parameters_generated.emit(
            normal_value,
            server_value,
        )

        self._append_log(
            "Mod parameters sent to the Systemd Panel."
        )

        QMessageBox.information(
            self,
            "Mod List Sent",
            "The generated mod parameters have been sent "
            "to the Systemd Panel.\n\n"
            "Go to the Systemd Service tab, review or edit "
            "the parameters, then use:\n\n"
            "Apply Parameters to ExecStart\n"
            "and then Save.",
        )

    # ==============================================================
    # Shutdown
    # ==============================================================

    def shutdown(self):
        self.jobs.shutdown()
