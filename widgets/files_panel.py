from pathlib import PurePosixPath
from datetime import datetime

from PySide6.QtCore import (
    Qt,
    QObject,
    QAbstractTableModel,
    QModelIndex,
    QThread,
    Signal,
    Slot,
    QTimer,
)

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QMenu,
    QMessageBox,
    QStyle,
    QTableView,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QWidget,
)

from config import AppConfig


# ============================================================
# SFTP WORKER
# ============================================================

class SFTPWorker(QObject):
    directory_loaded = Signal(int, str, list)
    operation_finished = Signal(int, str)
    error = Signal(int, str)

    def __init__(self, ssh, operation_id):
        super().__init__()

        self.ssh = ssh
        self.operation_id = operation_id

    def _sftp(self):
        if not self.ssh.is_connected():
            raise RuntimeError(
                "SSH connection is not active."
            )

        return self.ssh.sftp()

    @Slot(str)
    def list_directory(self, path):
        sftp = None

        try:
            sftp = self._sftp()

            entries = []

            for item in sftp.listdir_attr(path):
                full_path = str(
                    PurePosixPath(path) / item.filename
                )

                is_dir = bool(
                    item.st_mode & 0o040000
                )

                modified = ""

                if item.st_mtime:
                    modified = datetime.fromtimestamp(
                        item.st_mtime
                    ).strftime(
                        "%Y-%m-%d %H:%M"
                    )

                permissions = ""

                try:
                    permissions = (
                        item.longname.split()[0]
                    )
                except Exception:
                    pass

                entries.append({
                    "name": item.filename,
                    "path": full_path,
                    "is_dir": is_dir,
                    "size": (
                        item.st_size
                        if not is_dir
                        else 0
                    ),
                    "modified": modified,
                    "permissions": permissions,
                })

            entries.sort(
                key=lambda x: (
                    not x["is_dir"],
                    x["name"].lower(),
                )
            )

            self.directory_loaded.emit(
                self.operation_id,
                path,
                entries,
            )

        except Exception as e:
            self.error.emit(
                self.operation_id,
                str(e),
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    @Slot(str, str)
    def rename(self, old_path, new_path):
        sftp = None

        try:
            sftp = self._sftp()

            sftp.rename(
                old_path,
                new_path,
            )

            self.operation_finished.emit(
                self.operation_id,
                (
                    "Renamed to "
                    f"{PurePosixPath(new_path).name}"
                ),
            )

        except Exception as e:
            self.error.emit(
                self.operation_id,
                str(e),
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    @Slot(str)
    def mkdir(self, path):
        sftp = None

        try:
            sftp = self._sftp()

            sftp.mkdir(path)

            self.operation_finished.emit(
                self.operation_id,
                (
                    "Created folder "
                    f"{PurePosixPath(path).name}"
                ),
            )

        except Exception as e:
            self.error.emit(
                self.operation_id,
                str(e),
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    def _delete_recursive(self, sftp, path):
        attributes = sftp.stat(path)

        is_dir = bool(
            attributes.st_mode & 0o040000
        )

        if not is_dir:
            sftp.remove(path)
            return

        for item in sftp.listdir_attr(path):
            child = str(
                PurePosixPath(path) / item.filename
            )

            child_is_dir = bool(
                item.st_mode & 0o040000
            )

            if child_is_dir:
                self._delete_recursive(
                    sftp,
                    child,
                )
            else:
                sftp.remove(child)

        sftp.rmdir(path)

    @Slot(str)
    def delete(self, path):
        sftp = None

        try:
            sftp = self._sftp()

            self._delete_recursive(
                sftp,
                path,
            )

            self.operation_finished.emit(
                self.operation_id,
                (
                    "Deleted "
                    f"{PurePosixPath(path).name}"
                ),
            )

        except Exception as e:
            self.error.emit(
                self.operation_id,
                str(e),
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    @Slot(str, str)
    def download(self, remote_path, local_path):
        sftp = None

        try:
            sftp = self._sftp()

            sftp.get(
                remote_path,
                local_path,
            )

            self.operation_finished.emit(
                self.operation_id,
                (
                    "Downloaded "
                    f"{PurePosixPath(remote_path).name}"
                ),
            )

        except Exception as e:
            self.error.emit(
                self.operation_id,
                str(e),
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    @Slot(str, str)
    def upload(self, local_path, remote_path):
        sftp = None

        try:
            sftp = self._sftp()

            sftp.put(
                local_path,
                remote_path,
            )

            self.operation_finished.emit(
                self.operation_id,
                (
                    "Uploaded "
                    f"{PurePosixPath(local_path).name}"
                ),
            )

        except Exception as e:
            self.error.emit(
                self.operation_id,
                str(e),
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass


# ============================================================
# FILE MODEL
# ============================================================

class SFTPFileModel(QAbstractTableModel):
    HEADERS = [
        "Name",
        "Marks",
        "Size",
        "Type",
        "Modified",
        "Permissions",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        self.entries = []

        style = QApplication.style()

        self.folder_icon = style.standardIcon(
            QStyle.SP_DirIcon
        )

        self.file_icon = style.standardIcon(
            QStyle.SP_FileIcon
        )

        # Config markers are stored as the paths used by the
        # Config Editor.
        self.config_marked_files = set()

        # Wipe markers are stored relative to server_root.
        self.wipe_marked_files = set()

        # Needed to convert an entry's absolute remote path
        # into the relative path used by marked_wipe_files.
        self.server_root = ""

    def rowCount(
        self,
        parent=QModelIndex(),
    ):
        if parent.isValid():
            return 0

        return len(self.entries)

    def columnCount(
        self,
        parent=QModelIndex(),
    ):
        if parent.isValid():
            return 0

        return len(self.HEADERS)

    def headerData(
        self,
        section,
        orientation,
        role=Qt.DisplayRole,
    ):
        if role != Qt.DisplayRole:
            return None

        if orientation == Qt.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]

        return None

    # --------------------------------------------------------
    # MARKER PATH HELPERS
    # --------------------------------------------------------

    def _wipe_marker_for_path(self, path):
        """
        Convert an absolute remote path into the relative
        marker format used by config.marked_wipe_files.

        Example:

            server_root:
                /home/dayz/server

            file:
                /home/dayz/server/mpmissions/dayzOffline.chernarusplus/db/example.xml

            marker:
                mpmissions/dayzOffline.chernarusplus/db/example.xml
        """

        if not self.server_root:
            return None

        root = PurePosixPath(
            self.server_root
        )

        remote = PurePosixPath(path)

        try:
            relative = remote.relative_to(root)
        except ValueError:
            return None

        if str(relative) in ("", "."):
            return None

        return str(relative)

    def data(
        self,
        index,
        role=Qt.DisplayRole,
    ):
        if not index.isValid():
            return None

        row = index.row()
        column = index.column()

        if row < 0 or row >= len(self.entries):
            return None

        entry = self.entries[row]

        if role == Qt.DisplayRole:

            # ------------------------------------------------
            # NAME
            # ------------------------------------------------

            if column == 0:
                return entry["name"]

            # ------------------------------------------------
            # MARKS
            # ------------------------------------------------

            if column == 1:
                path = entry["path"]

                marks = []

                # Config marker uses the path directly.
                if path in self.config_marked_files:
                    marks.append("C")

                # Wipe marker uses a path relative to
                # server_root, so convert before checking.
                wipe_marker = self._wipe_marker_for_path(
                    path
                )

                if (
                    wipe_marker is not None
                    and wipe_marker
                    in self.wipe_marked_files
                ):
                    marks.append("W")

                return ", ".join(marks)

            # ------------------------------------------------
            # SIZE
            # ------------------------------------------------

            if column == 2:
                if entry["is_dir"]:
                    return ""

                return self._format_size(
                    entry["size"]
                )

            # ------------------------------------------------
            # TYPE
            # ------------------------------------------------

            if column == 3:
                return (
                    "Folder"
                    if entry["is_dir"]
                    else "File"
                )

            # ------------------------------------------------
            # MODIFIED
            # ------------------------------------------------

            if column == 4:
                return entry["modified"]

            # ------------------------------------------------
            # PERMISSIONS
            # ------------------------------------------------

            if column == 5:
                return entry["permissions"]

        elif role == Qt.DecorationRole:
            if column == 0:
                if entry["is_dir"]:
                    return self.folder_icon

                return self.file_icon

        elif role == Qt.TextAlignmentRole:
            if column == 1:
                return Qt.AlignCenter

            if column == 2:
                return (
                    Qt.AlignRight
                    | Qt.AlignVCenter
                )

        return None

    @staticmethod
    def _format_size(size):
        if size is None:
            return ""

        size = float(size)

        units = [
            "B",
            "KB",
            "MB",
            "GB",
            "TB",
        ]

        for unit in units:
            if size < 1024:
                if unit == "B":
                    return f"{int(size)} {unit}"

                return f"{size:.1f} {unit}"

            size /= 1024

        return f"{size:.1f} PB"

    def set_entries(self, entries):
        self.beginResetModel()

        self.entries = entries

        self.endResetModel()

    def set_markers(
        self,
        config_marked_files,
        wipe_marked_files,
        server_root=None,
    ):
        self.config_marked_files = set(
            config_marked_files or []
        )

        self.wipe_marked_files = set(
            wipe_marked_files or []
        )

        if server_root is not None:
            self.server_root = str(
                PurePosixPath(server_root)
            )

        # Refresh the complete Marks column.
        if self.entries:
            top_left = self.index(
                0,
                1,
            )

            bottom_right = self.index(
                len(self.entries) - 1,
                1,
            )

            self.dataChanged.emit(
                top_left,
                bottom_right,
                [Qt.DisplayRole],
            )

    def entry_at(self, row):
        if row < 0 or row >= len(self.entries):
            return None

        return self.entries[row]


# ============================================================
# FILES PANEL
# ============================================================

class FilesPanel(QWidget):
    open_in_config_editor = Signal(str)

    config_file_marked = Signal(str)
    config_file_unmarked = Signal(str)

    wipe_file_marked = Signal(str)
    wipe_file_unmarked = Signal(str)

    CONFIG_EXTENSIONS = {
        ".json",
        ".cfg",
        ".txt",
        ".xml",
        ".c",
    }

    DEFAULT_COLUMN_WIDTHS = [
        360,
        70,
        100,
        100,
        150,
        110,
    ]

    def __init__(
        self,
        ssh,
        config: AppConfig,
        parent=None,
    ):
        super().__init__(parent)

        self.ssh = ssh
        self.config = config

        self.current_path = ""

        self.history = []
        self.history_index = -1

        self.worker_thread = None
        self.worker = None

        self._operation_id = 0
        self._cleaning_up = False

        self.model = SFTPFileModel(self)

        self._build_ui()
        self._restore_column_widths()
        self._update_model_markers()
        self._update_ui_state()

    # ========================================================
    # UI
    # ========================================================

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        # ----------------------------------------------------
        # QUICK FOLDERS
        # ----------------------------------------------------

        quick_layout = QHBoxLayout()

        self.root_button = QPushButton(
            "Server Root"
        )

        self.profiles_button = QPushButton(
            "Profiles / Logs"
        )

        self.keys_button = QPushButton(
            "Keys"
        )

        self.missions_button = QPushButton(
            "MP Missions"
        )

        self.workshop_button = QPushButton(
            "Workshop"
        )

        quick_layout.addWidget(
            self.root_button
        )

        quick_layout.addWidget(
            self.profiles_button
        )

        quick_layout.addWidget(
            self.keys_button
        )

        quick_layout.addWidget(
            self.missions_button
        )

        quick_layout.addWidget(
            self.workshop_button
        )

        quick_layout.addStretch()

        main_layout.addLayout(
            quick_layout
        )

        self.root_button.clicked.connect(
            lambda: self.navigate_to(
                self.config.server_root
            )
        )

        self.profiles_button.clicked.connect(
            lambda: self.navigate_to(
                self.config.profiles_dir
            )
        )

        self.keys_button.clicked.connect(
            lambda: self.navigate_to(
                self.config.keys_dir
            )
        )

        self.missions_button.clicked.connect(
            lambda: self.navigate_to(
                self.config.mpmissions_dir
            )
        )

        self.workshop_button.clicked.connect(
            lambda: self.navigate_to(
                self.config.workshop_content_dir
            )
        )

        # ----------------------------------------------------
        # NAVIGATION
        # ----------------------------------------------------

        navigation_layout = QHBoxLayout()

        self.back_button = QPushButton("←")
        self.forward_button = QPushButton("→")
        self.up_button = QPushButton("↑")
        self.refresh_button = QPushButton(
            "Refresh"
        )

        self.back_button.setToolTip(
            "Back"
        )

        self.forward_button.setToolTip(
            "Forward"
        )

        self.up_button.setToolTip(
            "Parent directory"
        )

        navigation_layout.addWidget(
            self.back_button
        )

        navigation_layout.addWidget(
            self.forward_button
        )

        navigation_layout.addWidget(
            self.up_button
        )

        navigation_layout.addWidget(
            self.refresh_button
        )

        self.path_edit = QLineEdit()

        self.path_edit.setPlaceholderText(
            "Remote path..."
        )

        navigation_layout.addWidget(
            self.path_edit,
            1,
        )

        main_layout.addLayout(
            navigation_layout
        )

        self.back_button.clicked.connect(
            self.go_back
        )

        self.forward_button.clicked.connect(
            self.go_forward
        )

        self.up_button.clicked.connect(
            self.go_up
        )

        self.refresh_button.clicked.connect(
            self.refresh
        )

        self.path_edit.returnPressed.connect(
            self.go_to_path
        )

        # ----------------------------------------------------
        # FILE TABLE
        # ----------------------------------------------------

        self.file_view = QTableView()

        self.file_view.setModel(
            self.model
        )

        self.file_view.setSelectionBehavior(
            QTableView.SelectRows
        )

        self.file_view.setSelectionMode(
            QTableView.SingleSelection
        )

        self.file_view.setAlternatingRowColors(
            True
        )

        self.file_view.setSortingEnabled(
            False
        )

        header = self.file_view.horizontalHeader()

        # Every column can be resized manually.
        for column in range(6):
            header.setSectionResizeMode(
                column,
                QHeaderView.Interactive,
            )

        self.file_view.verticalHeader().setVisible(
            False
        )

        self.file_view.doubleClicked.connect(
            self.on_double_click
        )

        self.file_view.setContextMenuPolicy(
            Qt.CustomContextMenu
        )

        self.file_view.customContextMenuRequested.connect(
            self.show_context_menu
        )

        # Save column widths whenever the user drags
        # a separator.
        header.sectionResized.connect(
            self._on_column_resized
        )

        main_layout.addWidget(
            self.file_view,
            1,
        )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        self.status_label = QLabel(
            "Not connected"
        )

        main_layout.addWidget(
            self.status_label
        )

    # ========================================================
    # MARKER MODEL
    # ========================================================

    def _update_model_markers(self):
        """
        Update the table's marker state.

        IMPORTANT:
        Config markers are stored as absolute paths.

        Wipe markers are stored relative to server_root.

        The model receives server_root so it can correctly
        translate the displayed absolute path into the stored
        relative wipe marker.
        """

        self.model.set_markers(
            getattr(
                self.config,
                "marked_config_files",
                [],
            ),
            getattr(
                self.config,
                "marked_wipe_files",
                [],
            ),
            getattr(
                self.config,
                "server_root",
                "",
            ),
        )

    # ========================================================
    # COLUMN WIDTH PERSISTENCE
    # ========================================================

    def _restore_column_widths(self):
        widths = getattr(
            self.config,
            "files_column_widths",
            None,
        )

        if not isinstance(widths, list):
            widths = self.DEFAULT_COLUMN_WIDTHS

        if len(widths) != 6:
            widths = self.DEFAULT_COLUMN_WIDTHS

        header = self.file_view.horizontalHeader()

        for column, width in enumerate(widths):
            try:
                width = int(width)
            except (TypeError, ValueError):
                width = self.DEFAULT_COLUMN_WIDTHS[
                    column
                ]

            width = max(50, width)

            header.resizeSection(
                column,
                width,
            )

    @Slot(int, int, int)
    def _on_column_resized(
        self,
        logical_index,
        old_size,
        new_size,
    ):
        if self._cleaning_up:
            return

        widths = []

        header = self.file_view.horizontalHeader()

        for column in range(6):
            widths.append(
                header.sectionSize(column)
            )

        self.config.files_column_widths = widths

        self.config.save()

    # ========================================================
    # CONNECTION
    # ========================================================

    def set_connected(self, connected):
        if not connected:
            self.stop_worker()

            self.current_path = ""

            self.history.clear()
            self.history_index = -1

            self.model.set_entries([])

            self.path_edit.clear()

            self.status_label.setText(
                "Not connected"
            )

            self._update_ui_state()

            return

        self._update_model_markers()
        self._update_ui_state()

        if not self.current_path:
            self.navigate_to(
                self.config.server_root
            )

    # ========================================================
    # CONFIGURED PATHS
    # ========================================================

    @Slot(str)
    def set_profiles_path(self, path):
        """
        Update the Profiles / Logs location supplied by
        SystemdPanel.
        """

        path = (path or "").strip()

        if not path:
            return

        self.config.profiles_dir = str(
            PurePosixPath(path)
        )

        self.config.log_dir = (
            self.config.profiles_dir
        )

        self._update_ui_state()

    # ========================================================
    # NAVIGATION
    # ========================================================

    def navigate_to(
        self,
        path,
        add_history=True,
    ):
        if not path:
            return

        if not self.ssh.is_connected():
            self.status_label.setText(
                "Not connected"
            )
            return

        path = str(
            PurePosixPath(path)
        )

        if add_history:
            if (
                not self.history
                or self.history_index < 0
                or self.history[
                    self.history_index
                ] != path
            ):
                self.history = self.history[
                    : self.history_index + 1
                ]

                self.history.append(path)

                self.history_index = (
                    len(self.history) - 1
                )

        self.load_directory(path)

    def load_directory(self, path):
        if not self.ssh.is_connected():
            return

        if self.worker_thread is not None:
            return

        self._operation_id += 1

        operation_id = self._operation_id

        self.current_path = path

        self.path_edit.setText(
            path
        )

        self.status_label.setText(
            f"Loading {path}..."
        )

        self.file_view.setEnabled(
            False
        )

        self._update_ui_state()

        thread = QThread(self)

        worker = SFTPWorker(
            self.ssh,
            operation_id,
        )

        worker.moveToThread(thread)

        thread.started.connect(
            lambda: worker.list_directory(path)
        )

        worker.directory_loaded.connect(
            self.on_directory_loaded
        )

        worker.error.connect(
            self.on_worker_error
        )

        worker.operation_finished.connect(
            self.on_operation_finished
        )

        thread.finished.connect(
            worker.deleteLater
        )

        thread.finished.connect(
            self.on_worker_thread_finished
        )

        self.worker_thread = thread
        self.worker = worker

        thread.start()

    def go_back(self):
        if self.worker_thread is not None:
            return

        if self.history_index <= 0:
            return

        self.history_index -= 1

        self.load_directory(
            self.history[
                self.history_index
            ]
        )

        self._update_ui_state()

    def go_forward(self):
        if self.worker_thread is not None:
            return

        if (
            self.history_index < 0
            or self.history_index
            >= len(self.history) - 1
        ):
            return

        self.history_index += 1

        self.load_directory(
            self.history[
                self.history_index
            ]
        )

        self._update_ui_state()

    def go_up(self):
        if not self.current_path:
            return

        if self.worker_thread is not None:
            return

        parent = str(
            PurePosixPath(
                self.current_path
            ).parent
        )

        if parent == self.current_path:
            return

        self.navigate_to(parent)

    def go_to_path(self):
        path = self.path_edit.text().strip()

        if not path:
            return

        self.navigate_to(path)

    def refresh(self):
        if not self.current_path:
            return

        if self.worker_thread is not None:
            return

        self.load_directory(
            self.current_path
        )

    # ========================================================
    # DIRECTORY RESULTS
    # ========================================================

    @Slot(int, str, list)
    def on_directory_loaded(
        self,
        operation_id,
        path,
        entries,
    ):
        if operation_id != self._operation_id:
            return

        self.current_path = path

        self.path_edit.setText(
            path
        )

        self.model.set_entries(
            entries
        )

        self._update_model_markers()

        self.status_label.setText(
            f"{len(entries)} item(s)"
        )

        self.file_view.setEnabled(
            True
        )

        self._update_ui_state()

        self.stop_worker()

    # ========================================================
    # OPERATIONS
    # ========================================================

    def start_operation(
        self,
        target,
        *args,
    ):
        if self.worker_thread is not None:
            return

        self._operation_id += 1

        operation_id = self._operation_id

        thread = QThread(self)

        worker = SFTPWorker(
            self.ssh,
            operation_id,
        )

        worker.moveToThread(thread)

        method = getattr(
            worker,
            target,
        )

        thread.started.connect(
            lambda: method(*args)
        )

        worker.operation_finished.connect(
            self.on_operation_finished
        )

        worker.error.connect(
            self.on_worker_error
        )

        thread.finished.connect(
            worker.deleteLater
        )

        thread.finished.connect(
            self.on_worker_thread_finished
        )

        self.worker_thread = thread
        self.worker = worker

        self.file_view.setEnabled(
            False
        )

        self._update_ui_state()

        thread.start()

    @Slot(int, str)
    def on_operation_finished(
        self,
        operation_id,
        message,
    ):
        if operation_id != self._operation_id:
            return

        self.status_label.setText(
            message
        )

        QTimer.singleShot(
            0,
            self._refresh_after_operation,
        )

    def _refresh_after_operation(self):
        if self.worker_thread is not None:
            QTimer.singleShot(
                50,
                self._refresh_after_operation,
            )
            return

        if (
            self.current_path
            and self.ssh.is_connected()
        ):
            self.load_directory(
                self.current_path
            )

    @Slot(int, str)
    def on_worker_error(
        self,
        operation_id,
        message,
    ):
        if operation_id != self._operation_id:
            return

        self.status_label.setText(
            f"Error: {message}"
        )

        QMessageBox.critical(
            self,
            "SFTP Error",
            message,
        )

        self.stop_worker()

    # ========================================================
    # WORKER LIFECYCLE
    # ========================================================

    def stop_worker(self):
        thread = self.worker_thread

        if thread is None:
            return

        thread.quit()

    @Slot()
    def on_worker_thread_finished(self):
        thread = self.sender()

        if thread is not self.worker_thread:
            return

        self.worker_thread = None
        self.worker = None

        self.file_view.setEnabled(
            True
        )

        self._update_ui_state()

    # ========================================================
    # CLEANUP
    # ========================================================

    def cleanup(self):
        self._cleaning_up = True

        try:
            header = self.file_view.horizontalHeader()

            self.config.files_column_widths = [
                header.sectionSize(column)
                for column in range(6)
            ]

            self.config.save()

        except Exception:
            pass

        self.stop_worker()

    # ========================================================
    # DOUBLE CLICK
    # ========================================================

    @Slot(QModelIndex)
    def on_double_click(self, index):
        entry = self.model.entry_at(
            index.row()
        )

        if not entry:
            return

        if entry["is_dir"]:
            self.navigate_to(
                entry["path"]
            )
            return

        path = entry["path"]

        if self.is_config_file(path):
            self.open_in_config_editor.emit(
                path
            )
            return

        self.download_file(path)

    # ========================================================
    # CONTEXT MENU
    # ========================================================

    def show_context_menu(self, position):
        index = self.file_view.indexAt(
            position
        )

        menu = QMenu(self)

        refresh_action = menu.addAction(
            "Refresh"
        )

        menu.addSeparator()

        new_folder_action = menu.addAction(
            "New Folder"
        )

        upload_action = menu.addAction(
            "Upload..."
        )

        selected_entry = None

        if index.isValid():
            selected_entry = self.model.entry_at(
                index.row()
            )

        config_action = None
        wipe_action = None
        download_action = None
        rename_action = None
        delete_action = None

        if selected_entry:
            path = selected_entry["path"]

            menu.addSeparator()

            # ------------------------------------------------
            # CONFIG EDITOR MARK
            # ------------------------------------------------

            if (
                not selected_entry["is_dir"]
                and self.is_config_file(path)
            ):
                if self.is_config_marked(path):
                    config_action = menu.addAction(
                        "Remove from Config Editor"
                    )
                else:
                    config_action = menu.addAction(
                        "Add to Config Editor"
                    )

            # ------------------------------------------------
            # WIPE MARK
            # ------------------------------------------------

            if not selected_entry["is_dir"]:
                if self.is_wipe_marked(path):
                    wipe_action = menu.addAction(
                        "Remove from Wipe List"
                    )
                else:
                    wipe_action = menu.addAction(
                        "Add to Wipe List"
                    )

            if (
                config_action is not None
                or wipe_action is not None
            ):
                menu.addSeparator()

            # ------------------------------------------------
            # FILE OPERATIONS
            # ------------------------------------------------

            if not selected_entry["is_dir"]:
                download_action = menu.addAction(
                    "Download..."
                )

            rename_action = menu.addAction(
                "Rename"
            )

            delete_action = menu.addAction(
                "Delete"
            )

        menu.addSeparator()

        action = menu.exec(
            self.file_view.viewport().mapToGlobal(
                position
            )
        )

        if action == refresh_action:
            self.refresh()

        elif action == new_folder_action:
            self.new_folder()

        elif action == upload_action:
            self.upload_file()

        elif selected_entry:
            path = selected_entry["path"]

            if action == config_action:
                if self.is_config_marked(path):
                    self.unmark_config_file(path)
                else:
                    self.mark_config_file(path)

            elif action == wipe_action:
                if self.is_wipe_marked(path):
                    self.unmark_wipe_file(path)
                else:
                    self.mark_wipe_file(path)

            elif action == download_action:
                self.download_file(path)

            elif action == rename_action:
                self.rename_file(path)

            elif action == delete_action:
                self.delete_file(path)

    # ========================================================
    # CONFIG EDITOR MARKING
    # ========================================================

    def is_config_file(self, path):
        suffix = (
            PurePosixPath(path)
            .suffix
            .lower()
        )

        return suffix in self.CONFIG_EXTENSIONS

    def is_config_marked(self, path):
        return (
            path
            in getattr(
                self.config,
                "marked_config_files",
                [],
            )
        )

    def mark_config_file(self, path):
        if not self.is_config_file(path):
            return

        if path in self.config.marked_config_files:
            return

        self.config.marked_config_files.append(
            path
        )

        self.config.save()

        self.config_file_marked.emit(
            path
        )

        self._update_model_markers()

        self.status_label.setText(
            "Added to Config Editor: "
            f"{PurePosixPath(path).name}"
        )

    def unmark_config_file(self, path):
        if (
            path
            not in self.config.marked_config_files
        ):
            return

        self.config.marked_config_files.remove(
            path
        )

        self.config.save()

        self.config_file_unmarked.emit(
            path
        )

        self._update_model_markers()

        self.status_label.setText(
            "Removed from Config Editor: "
            f"{PurePosixPath(path).name}"
        )

    # ========================================================
    # WIPE MARKING
    # ========================================================

    def _server_root_path(self):
        return PurePosixPath(
            self.config.server_root
        )

    def _wipe_marker_path(self, remote_path):
        """
        Convert an absolute remote path into a path relative
        to server_root.

        Wipe markers are stored in this relative format so
        they survive changes to the absolute server root.
        """

        root = self._server_root_path()
        path = PurePosixPath(remote_path)

        try:
            relative = path.relative_to(root)
        except ValueError:
            return None

        if str(relative) in ("", "."):
            return None

        return str(relative)

    def _wipe_remote_path(self, marker):
        """
        Convert a stored wipe marker back into an absolute
        remote path.

        Returns None if the marker is unsafe or attempts to
        escape server_root.
        """

        marker = str(marker or "").strip()

        if not marker:
            return None

        marker_path = PurePosixPath(marker)

        if marker_path.is_absolute():
            return None

        root = self._server_root_path()

        candidate = root / marker_path

        try:
            candidate.relative_to(root)
        except ValueError:
            return None

        return str(candidate)

    def is_wipe_marked(self, path):
        marker = self._wipe_marker_path(path)

        if marker is None:
            return False

        return (
            marker
            in getattr(
                self.config,
                "marked_wipe_files",
                [],
            )
        )

    def mark_wipe_file(self, path):
        marker = self._wipe_marker_path(path)

        if marker is None:
            QMessageBox.warning(
                self,
                "Cannot Mark File",
                "The selected file is outside "
                "the configured server root.",
            )
            return

        marked_files = getattr(
            self.config,
            "marked_wipe_files",
            None,
        )

        if marked_files is None:
            self.config.marked_wipe_files = []
            marked_files = (
                self.config.marked_wipe_files
            )

        if marker in marked_files:
            return

        marked_files.append(marker)

        self.config.save()

        self.wipe_file_marked.emit(
            path
        )

        self._update_model_markers()

        self.status_label.setText(
            "Added to Wipe List: "
            f"{PurePosixPath(path).name}"
        )

    def unmark_wipe_file(self, path):
        marker = self._wipe_marker_path(path)

        if marker is None:
            return

        marked_files = getattr(
            self.config,
            "marked_wipe_files",
            [],
        )

        if marker not in marked_files:
            return

        marked_files.remove(marker)

        self.config.save()

        self.wipe_file_unmarked.emit(
            path
        )

        self._update_model_markers()

        self.status_label.setText(
            "Removed from Wipe List: "
            f"{PurePosixPath(path).name}"
        )

    # ========================================================
    # NEW FOLDER
    # ========================================================

    def new_folder(self):
        if not self.current_path:
            return

        if self.worker_thread is not None:
            return

        name, ok = QInputDialog.getText(
            self,
            "New Folder",
            "Folder name:",
        )

        if not ok:
            return

        name = name.strip()

        if not name:
            return

        if "/" in name:
            QMessageBox.warning(
                self,
                "Invalid folder name",
                "Folder names cannot contain '/'.",
            )
            return

        path = str(
            PurePosixPath(
                self.current_path
            ) / name
        )

        self.start_operation(
            "mkdir",
            path,
        )

    # ========================================================
    # UPLOAD
    # ========================================================

    def upload_file(self):
        if not self.current_path:
            return

        if self.worker_thread is not None:
            return

        local_path, _ = QFileDialog.getOpenFileName(
            self,
            "Upload File",
        )

        if not local_path:
            return

        filename = PurePosixPath(
            local_path
        ).name

        remote_path = str(
            PurePosixPath(
                self.current_path
            ) / filename
        )

        self.start_operation(
            "upload",
            local_path,
            remote_path,
        )

    # ========================================================
    # DOWNLOAD
    # ========================================================

    def download_file(self, remote_path):
        if self.worker_thread is not None:
            return

        filename = PurePosixPath(
            remote_path
        ).name

        local_path, _ = QFileDialog.getSaveFileName(
            self,
            "Download File",
            filename,
        )

        if not local_path:
            return

        self.start_operation(
            "download",
            remote_path,
            local_path,
        )

    # ========================================================
    # RENAME
    # ========================================================

    def rename_file(self, old_path):
        if self.worker_thread is not None:
            return

        old_name = PurePosixPath(
            old_path
        ).name

        new_name, ok = QInputDialog.getText(
            self,
            "Rename",
            "New name:",
            text=old_name,
        )

        if not ok:
            return

        new_name = new_name.strip()

        if not new_name:
            return

        if "/" in new_name:
            QMessageBox.warning(
                self,
                "Invalid name",
                "Names cannot contain '/'.",
            )
            return

        parent = PurePosixPath(
            old_path
        ).parent

        new_path = str(
            parent / new_name
        )

        if old_path == new_path:
            return

        config_changed = False
        wipe_changed = False

        # ----------------------------------------------------
        # CONFIG EDITOR MARKER
        # ----------------------------------------------------

        if (
            old_path
            in self.config.marked_config_files
        ):
            self.config.marked_config_files.remove(
                old_path
            )

            self.config.marked_config_files.append(
                new_path
            )

            config_changed = True

            self.config_file_unmarked.emit(
                old_path
            )

            self.config_file_marked.emit(
                new_path
            )

        # ----------------------------------------------------
        # WIPE MARKER
        # ----------------------------------------------------

        old_wipe_marker = self._wipe_marker_path(
            old_path
        )

        new_wipe_marker = self._wipe_marker_path(
            new_path
        )

        marked_wipe_files = getattr(
            self.config,
            "marked_wipe_files",
            [],
        )

        if (
            old_wipe_marker
            and new_wipe_marker
            and old_wipe_marker
            in marked_wipe_files
        ):
            marked_wipe_files.remove(
                old_wipe_marker
            )

            if new_wipe_marker not in marked_wipe_files:
                marked_wipe_files.append(
                    new_wipe_marker
                )

            wipe_changed = True

            self.wipe_file_unmarked.emit(
                old_path
            )

            self.wipe_file_marked.emit(
                new_path
            )

        if config_changed or wipe_changed:
            self.config.save()

        self._update_model_markers()

        self.start_operation(
            "rename",
            old_path,
            new_path,
        )

    # ========================================================
    # DELETE
    # ========================================================

    def delete_file(self, path):
        if self.worker_thread is not None:
            return

        name = PurePosixPath(path).name

        answer = QMessageBox.question(
            self,
            "Delete",
            f"Delete '{name}'?\n\n"
            "This will permanently remove the "
            "remote file or folder.",
            QMessageBox.Yes
            | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        markers_changed = False

        # ----------------------------------------------------
        # CONFIG EDITOR MARKER
        # ----------------------------------------------------

        if (
            path
            in self.config.marked_config_files
        ):
            self.config.marked_config_files.remove(
                path
            )

            markers_changed = True

            self.config_file_unmarked.emit(
                path
            )

        # ----------------------------------------------------
        # WIPE MARKER
        # ----------------------------------------------------

        wipe_marker = self._wipe_marker_path(
            path
        )

        marked_wipe_files = getattr(
            self.config,
            "marked_wipe_files",
            [],
        )

        if (
            wipe_marker
            and wipe_marker
            in marked_wipe_files
        ):
            marked_wipe_files.remove(
                wipe_marker
            )

            markers_changed = True

            self.wipe_file_unmarked.emit(
                path
            )

        if markers_changed:
            self.config.save()

        self._update_model_markers()

        self.start_operation(
            "delete",
            path,
        )

    # ========================================================
    # UI STATE
    # ========================================================

    def _update_ui_state(self):
        connected = self.ssh.is_connected()
        busy = self.worker_thread is not None

        self.root_button.setEnabled(
            connected and not busy
        )

        self.profiles_button.setEnabled(
            connected and not busy
        )

        self.keys_button.setEnabled(
            connected and not busy
        )

        self.missions_button.setEnabled(
            connected and not busy
        )

        self.workshop_button.setEnabled(
            connected and not busy
        )

        self.back_button.setEnabled(
            connected
            and not busy
            and self.history_index > 0
        )

        self.forward_button.setEnabled(
            connected
            and not busy
            and (
                self.history_index >= 0
                and self.history_index
                < len(self.history) - 1
            )
        )

        self.up_button.setEnabled(
            connected
            and not busy
            and bool(self.current_path)
            and (
                str(
                    PurePosixPath(
                        self.current_path
                    ).parent
                )
                != self.current_path
            )
        )

        self.refresh_button.setEnabled(
            connected and not busy
        )

        self.path_edit.setEnabled(
            connected and not busy
        )
