from pathlib import Path
from urllib.request import Request, urlopen
import json
import re
import shlex

from PySide6.QtCore import QUrl
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSizePolicy,
    QSpinBox,
)
from PySide6.QtWebEngineWidgets import QWebEngineView


class MapPanel(QWidget):
    MAP_FILES = {
        "ChernarusPlus/map.png":
            "https://github.com/BohemiaInteractive/DayZ-Central-Economy/raw/master/CETool/ChernarusPlus/map.png",

        "Enoch/map.png":
            "https://github.com/BohemiaInteractive/DayZ-Central-Economy/raw/master/CETool/Enoch/map.png",

        "Esseker/map.png":
            "https://github.com/InclementDab/Esseker-Server/raw/main/Central%20Economy%20Tool/map.png",

        "html2canvas.min.js":
            "https://html2canvas.hertzen.com/dist/html2canvas.min.js",

        "FileSaver.min.js":
            "https://cdnjs.cloudflare.com/ajax/libs/FileSaver.js/2.0.5/FileSaver.min.js",
    }

    def __init__(
        self,
        ssh,
        config,
    ):
        super().__init__()

        self.ssh = ssh
        self.config = config
        self.connected = False

        self.map_dir = (
            Path(__file__).resolve().parent.parent
            / "map"
        )

        self.assets_dir = self.map_dir / "assets"

        self.data_dir = (
            self.map_dir
            / "data"
        )

        self.heatmap_data_file = (
            self.data_dir
            / "heatmap_data.json"
        )

        self.download_button = QPushButton(
            "Download Map Assets"
        )

        self.download_button.setFixedSize(
            140,
            24,
        )

        self.fetch_button = QPushButton(
            "Fetch Heatmap Data"
        )

        self.fetch_button.setFixedSize(
            140,
            24,
        )

        # ====================================================
        # HEATMAP TRACKING SETTINGS
        # ====================================================

        self.heatmap_label = QLabel(
            "Heatmap Tracking:"
        )

        self.player_heatmap_label = QLabel(
            "Player:"
        )

        self.player_heatmap_spin = QSpinBox()

        self.player_heatmap_spin.setRange(
            1,
            86400,
        )

        self.player_heatmap_spin.setValue(
            120
        )

        self.player_heatmap_spin.setSuffix(
            " seconds"
        )

        self.player_heatmap_spin.setFixedWidth(
            115
        )

        self.vehicle_heatmap_label = QLabel(
            "Vehicle:"
        )

        self.vehicle_heatmap_spin = QSpinBox()

        self.vehicle_heatmap_spin.setRange(
            1,
            86400,
        )

        self.vehicle_heatmap_spin.setValue(
            30
        )

        self.vehicle_heatmap_spin.setSuffix(
            " seconds"
        )

        self.vehicle_heatmap_spin.setFixedWidth(
            115
        )

        self.apply_heatmap_button = QPushButton(
            "Apply"
        )

        self.apply_heatmap_button.setFixedSize(
            70,
            24,
        )

        self.status_label = QLabel(
            "Map assets can be downloaded below."
        )

        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        controls = QHBoxLayout()

        controls.setContentsMargins(
            4,
            2,
            4,
            2,
        )

        controls.setSpacing(6)

        controls.addWidget(
            self.download_button
        )

        controls.addWidget(
            self.fetch_button
        )

        controls.addSpacing(
            12
        )

        controls.addWidget(
            self.heatmap_label
        )

        controls.addWidget(
            self.player_heatmap_label
        )

        controls.addWidget(
            self.player_heatmap_spin
        )

        controls.addWidget(
            self.vehicle_heatmap_label
        )

        controls.addWidget(
            self.vehicle_heatmap_spin
        )

        controls.addWidget(
            self.apply_heatmap_button
        )

        controls.addWidget(
            self.status_label
        )

        controls.addStretch()

        self.web = QWebEngineView(self)

        self.web.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        # Match the WebEngine background to the existing
        # Qt application's window/background color.
        bg_color = self.palette().color(
            QPalette.ColorRole.Window
        )

        # Match the WebEngine page background.
        self.web.page().setBackgroundColor(
            bg_color
        )

        # Match the WebEngine and its scrollbars to the
        # existing application palette.
        self.web.setStyleSheet(
            f"""
            QWebEngineView {{
                background-color: {bg_color.name()};
            }}

            QScrollBar:vertical {{
                background: {bg_color.name()};
                width: 12px;
                margin: 0px;
            }}

            QScrollBar::handle:vertical {{
                background: {self.palette().color(QPalette.ColorRole.Mid).name()};
                min-height: 20px;
                border-radius: 6px;
                margin: 2px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: {self.palette().color(QPalette.ColorRole.Dark).name()};
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}

            QScrollBar:horizontal {{
                background: {bg_color.name()};
                height: 12px;
                margin: 0px;
            }}

            QScrollBar::handle:horizontal {{
                background: {self.palette().color(QPalette.ColorRole.Mid).name()};
                min-width: 20px;
                border-radius: 6px;
                margin: 2px;
            }}

            QScrollBar::handle:horizontal:hover {{
                background: {self.palette().color(QPalette.ColorRole.Dark).name()};
            }}

            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}

            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
            """
        )

        self.setStyleSheet(
            f"background-color: {bg_color.name()};"
        )

        layout = QVBoxLayout(self)

        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        layout.setSpacing(0)

        layout.addLayout(
            controls,
            0,
        )

        layout.addWidget(
            self.web,
            1,
        )

        self.download_button.clicked.connect(
            self.download_assets
        )

        self.fetch_button.clicked.connect(
            self.fetch_heatmap_data
        )

        self.apply_heatmap_button.clicked.connect(
            self.apply_heatmap_settings
        )

        index_file = (
            self.map_dir
            / "index.html"
        )

        self.web.setUrl(
            QUrl.fromLocalFile(
                str(index_file)
            )
        )

    # ========================================================
    # CONNECTION STATE
    # ========================================================

    def set_connected(
        self,
        connected,
    ):
        """
        Called by MainWindow whenever the shared SSH connection
        changes state.
        """

        self.connected = connected

        if connected:
            self.status_label.setText(
                "Connected. Click Fetch Heatmap Data."
            )

        else:
            self.status_label.setText(
                "Map assets can be downloaded below."
            )

    # ========================================================
    # HEATMAP TRACKING SETTINGS
    # ========================================================

    def apply_heatmap_settings(self):
        """
        Write the heatmap tracking settings to:

            <server_root>/serverDZ.cfg

        The DayZ systemd service MUST NOT be active.

        The service is checked using:

            systemctl is-active <configured service>

        No attempt is made to stop, start, or restart the service.
        """

        if not self.ssh.is_connected():
            print(
                "MapPanel: SSH is not connected."
            )

            self.status_label.setText(
                "Not connected. Connect to the server first."
            )

            return

        service_name = getattr(
            self.config,
            "systemd_service",
            "",
        )

        if not service_name:
            print(
                "MapPanel: systemd service is not configured."
            )

            self.status_label.setText(
                "Cannot apply settings: systemd service is not configured."
            )

            return

        self.apply_heatmap_button.setEnabled(
            False
        )

        self.status_label.setText(
            "Checking DayZ service status..."
        )

        try:
            # ------------------------------------------------
            # CHECK WHETHER THE DAYZ SERVICE IS RUNNING
            # ------------------------------------------------

            active_command = (
                "systemctl is-active "
                + shlex.quote(
                    service_name
                )
                + " 2>/dev/null || true"
            )

            (
                active_code,
                active_out,
                active_err,
            ) = self.ssh.exec(
                active_command
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

            print(
                f"MapPanel: DayZ service "
                f"'{service_name}' state: "
                f"{active_state}"
            )

            # ------------------------------------------------
            # DO NOT MODIFY CONFIG WHILE SERVER IS RUNNING
            # ------------------------------------------------

            if active_state == "active":

                print(
                    "MapPanel: Refusing to modify "
                    "serverDZ.cfg because the "
                    "DayZ service is running."
                )

                self.status_label.setText(
                    "Server is running. Stop the DayZ server before applying settings."
                )

                return

            # ------------------------------------------------
            # FIND SERVER ROOT
            # ------------------------------------------------

            server_root = getattr(
                self.config,
                "server_root",
                "",
            )

            if not server_root:
                print(
                    "MapPanel: server_root is not configured."
                )

                self.status_label.setText(
                    "Cannot apply settings: server root is not configured."
                )

                return

            remote_cfg_path = (
                server_root.rstrip("/")
                + "/serverDZ.cfg"
            )

            player_value = (
                self.player_heatmap_spin.value()
            )

            vehicle_value = (
                self.vehicle_heatmap_spin.value()
            )

            self.status_label.setText(
                "Reading serverDZ.cfg..."
            )

            print(
                f"MapPanel: Reading "
                f"{remote_cfg_path}"
            )

            # ------------------------------------------------
            # READ REMOTE CONFIG
            # ------------------------------------------------

            cfg_text = self.ssh.read_file(
                remote_cfg_path
            )

            # ------------------------------------------------
            # UPDATE PLAYER SETTING
            # ------------------------------------------------

            player_pattern = re.compile(
                r"^(\s*heatmapTickTime\s*=\s*)[^;]+(;.*)$",
                re.MULTILINE,
            )

            player_replacement = (
                rf"\g<1>{player_value}\g<2>"
            )

            cfg_text, player_count = (
                player_pattern.subn(
                    player_replacement,
                    cfg_text,
                )
            )

            # ------------------------------------------------
            # UPDATE VEHICLE SETTING
            # ------------------------------------------------

            vehicle_pattern = re.compile(
                r"^(\s*heatmapTickTimeVehicle\s*=\s*)[^;]+(;.*)$",
                re.MULTILINE,
            )

            vehicle_replacement = (
                rf"\g<1>{vehicle_value}\g<2>"
            )

            cfg_text, vehicle_count = (
                vehicle_pattern.subn(
                    vehicle_replacement,
                    cfg_text,
                )
            )

            # ------------------------------------------------
            # ADD MISSING SETTINGS
            # ------------------------------------------------

            additions = []

            if player_count == 0:
                additions.append(
                    f"heatmapTickTime = {player_value};"
                )

            if vehicle_count == 0:
                additions.append(
                    f"heatmapTickTimeVehicle = {vehicle_value};"
                )

            if additions:

                if cfg_text and not cfg_text.endswith(
                    "\n"
                ):
                    cfg_text += "\n"

                cfg_text += (
                    "\n"
                    + "\n".join(
                        additions
                    )
                    + "\n"
                )

            # ------------------------------------------------
            # WRITE REMOTE CONFIG
            # ------------------------------------------------

            self.status_label.setText(
                "Writing serverDZ.cfg..."
            )

            print(
                f"MapPanel: Writing "
                f"{remote_cfg_path}"
            )

            self.ssh.write_file(
                remote_cfg_path,
                cfg_text,
            )

            print(
                "MapPanel: Heatmap tracking settings "
                "updated successfully."
            )

            self.status_label.setText(
                f"Heatmap settings applied: "
                f"Player {player_value}s, "
                f"Vehicle {vehicle_value}s"
            )

        except Exception as e:

            print(
                f"MapPanel: Failed to apply "
                f"heatmap settings: {e}"
            )

            self.status_label.setText(
                f"Heatmap settings failed: {e}"
            )

        finally:

            self.apply_heatmap_button.setEnabled(
                True
            )

    # ========================================================
    # HEATMAP DATA
    # ========================================================

    def fetch_heatmap_data(self):
        """
        Fetch all DayZ heatmap JSON files from:

            <profiles_dir>/dzmanager

        The profiles directory comes directly from AppConfig.

        All m_WayPoints and m_DeathPoints entries are merged
        into one local JSON file for the map.
        """

        if not self.ssh.is_connected():
            print(
                "MapPanel: SSH is not connected."
            )

            self.status_label.setText(
                "Not connected. Connect to the server first."
            )

            return

        profiles_dir = getattr(
            self.config,
            "profiles_dir",
            "",
        )

        if not profiles_dir:
            print(
                "MapPanel: profiles_dir is not configured."
            )

            self.status_label.setText(
                "Heatmap fetch failed: profiles directory is not configured."
            )

            return

        remote_dir = (
            profiles_dir.rstrip("/")
            + "/dzmanager"
        )

        self.fetch_button.setEnabled(
            False
        )

        self.status_label.setText(
            "Fetching heatmap data..."
        )

        try:
            sftp = self.ssh.sftp()

            try:
                entries = sftp.listdir_attr(
                    remote_dir
                )

                heatmap_files = [
                    entry
                    for entry in entries
                    if entry.filename.endswith(
                        "_Heatmap.json"
                    )
                ]

                heatmap_files.sort(
                    key=lambda entry: entry.filename
                )

                if not heatmap_files:
                    self.status_label.setText(
                        "No heatmap files found."
                    )

                    print(
                        f"No heatmap files found in "
                        f"{remote_dir}"
                    )

                    return

                waypoints = []
                deathpoints = []

                loaded_files = 0
                failed_files = 0

                for entry in heatmap_files:

                    remote_file = (
                        remote_dir
                        + "/"
                        + entry.filename
                    )

                    self.status_label.setText(
                        f"Loading {entry.filename}..."
                    )

                    try:
                        with sftp.open(
                            remote_file,
                            "r",
                        ) as file:

                            raw_data = file.read()

                        if isinstance(
                            raw_data,
                            bytes,
                        ):
                            raw_data = raw_data.decode(
                                "utf-8",
                                errors="replace",
                            )

                        data = json.loads(
                            raw_data
                        )

                        file_waypoints = data.get(
                            "m_WayPoints",
                            [],
                        )

                        file_deathpoints = data.get(
                            "m_DeathPoints",
                            [],
                        )

                        if isinstance(
                            file_waypoints,
                            list,
                        ):
                            waypoints.extend(
                                file_waypoints
                            )

                        if isinstance(
                            file_deathpoints,
                            list,
                        ):
                            deathpoints.extend(
                                file_deathpoints
                            )

                        loaded_files += 1

                        print(
                            f"Loaded heatmap: "
                            f"{entry.filename}"
                        )

                    except Exception as e:

                        failed_files += 1

                        print(
                            f"Failed to load "
                            f"{entry.filename}: {e}"
                        )

            finally:
                sftp.close()

            # ------------------------------------------------
            # SAVE MERGED DATA
            # ------------------------------------------------

            self.data_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            merged_data = {
                "m_WayPoints": waypoints,
                "m_DeathPoints": deathpoints,
            }

            self.heatmap_data_file.write_text(
                json.dumps(
                    merged_data
                ),
                encoding="utf-8",
            )

            print(
                f"Heatmap data saved to: "
                f"{self.heatmap_data_file}"
            )

            print(
                f"Heatmap files loaded: "
                f"{loaded_files}"
            )

            print(
                f"Heatmap files failed: "
                f"{failed_files}"
            )

            print(
                f"Waypoint groups: "
                f"{len(waypoints)}"
            )

            print(
                f"Death points: "
                f"{len(deathpoints)}"
            )

            self.status_label.setText(
                f"Heatmap loaded: "
                f"{loaded_files} files, "
                f"{len(waypoints)} waypoint groups, "
                f"{len(deathpoints)} deaths"
            )

            # ------------------------------------------------
            # SEND THE MERGED DATA TO THE HTML MAP
            # ------------------------------------------------

            javascript_data = json.dumps(
                merged_data
            )

            self.web.page().runJavaScript(
                f"loadHeatmapData({javascript_data});"
            )

        except Exception as e:

            print(
                f"Failed to fetch heatmap data: {e}"
            )

            self.status_label.setText(
                f"Heatmap fetch failed: {e}"
            )

        finally:

            self.fetch_button.setEnabled(
                True
            )

    # ========================================================
    # MAP ASSETS
    # ========================================================

    def download_assets(self):
        self.download_button.setEnabled(False)

        self.status_label.setText(
            "Downloading map assets..."
        )

        self.assets_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        success = True
        downloaded = 0
        skipped = 0

        for relative_path, url in self.MAP_FILES.items():

            destination = (
                self.assets_dir
                / relative_path
            )

            if (
                destination.exists()
                and destination.stat().st_size > 0
            ):
                skipped += 1
                continue

            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.status_label.setText(
                f"Downloading {relative_path}..."
            )

            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent":
                            "DayZ Server Manager"
                    },
                )

                with urlopen(
                    request,
                    timeout=30,
                ) as response:

                    data = response.read()

                destination.write_bytes(
                    data
                )

                downloaded += 1

                print(
                    f"Downloaded: {destination}"
                )

            except Exception as e:

                success = False

                print(
                    f"Failed to download "
                    f"{relative_path}: {e}"
                )

        self.download_button.setEnabled(
            True
        )

        if success:

            self.status_label.setText(
                f"Map assets ready. "
                f"Downloaded: {downloaded}, "
                f"Already present: {skipped}"
            )

        else:

            self.status_label.setText(
                "Some map assets could not be "
                "downloaded. Check the console."
            )
