from PySide6.QtWidgets import (
    QWidget,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QCheckBox,
)


class SettingsPanel(QWidget):
    def __init__(self, config, on_saved):
        super().__init__()

        self.config = config
        self.on_saved = on_saved

        outer = QVBoxLayout(self)
        form = QFormLayout()

        self.fields = {}

        specs = [
            # ====================================================
            # SSH
            # ====================================================

            ("host", "SSH host"),
            ("port", "SSH port"),
            ("username", "SSH username"),
            ("key_path", "SSH private key path"),

            # ====================================================
            # SYSTEMD
            # ====================================================

            ("systemd_service", "systemd service name"),

            # ====================================================
            # SERVER PATHS
            # ====================================================

            ("server_root", "Server install root (remote)"),
            ("profiles_dir", "Profiles / logs dir (remote)"),
            ("keys_dir", "Keys dir (remote)"),
            ("mpmissions_dir", "MPMissions dir (remote)"),
            ("log_dir", "Log dir to clear (remote)"),

            # ====================================================
            # STEAM / WORKSHOP
            # ====================================================

            ("steamcmd_path", "steamcmd path or command (remote)"),
            ("steam_user", "Steam login (anonymous or account)"),
            ("workshop_content_dir", "Workshop content dir (remote)"),

            # ====================================================
            # BATTLEYE RCON
            # ====================================================

            ("rcon_host", "RCon host"),
            ("rcon_password", "RCon password"),
        ]

        for attr, label in specs:
            value = getattr(
                config,
                attr,
                "",
            )

            edit = QLineEdit(
                str(value)
            )

            self.fields[attr] = edit

            form.addRow(
                label,
                edit,
            )

        # ========================================================
        # RCON PASSWORD
        # ========================================================

        self.fields["rcon_password"].setEchoMode(
            QLineEdit.Password
        )

        # ========================================================
        # RCON AUTO RECONNECT
        # ========================================================

        self.rcon_auto_reconnect_checkbox = QCheckBox(
            "Automatically reconnect when the BattlEye RCon connection drops"
        )

        self.rcon_auto_reconnect_checkbox.setChecked(
            bool(
                getattr(
                    config,
                    "rcon_auto_reconnect",
                    True,
                )
            )
        )

        form.addRow(
            "RCon reconnect",
            self.rcon_auto_reconnect_checkbox,
        )

        # ========================================================
        # RCON PORT INFORMATION
        # ========================================================

        rcon_port_note = QLabel(
            "RCon port is detected automatically from "
            "<server_root>/BattlEye/BEServer_x64.cfg "
            "using the RConPort setting. "
            "If RConPort is not found, port 2305 is used."
        )

        rcon_port_note.setWordWrap(
            True
        )

        rcon_port_note.setStyleSheet(
            "color: gray;"
        )

        form.addRow(
            "",
            rcon_port_note,
        )

        outer.addLayout(
            form
        )

        # ========================================================
        # STEAM API NOTE
        # ========================================================

        api_key_note = QLabel(
            'Get a free Steam Web API key at '
            '<a href="https://steamcommunity.com/dev/apikey">'
            'steamcommunity.com/dev/apikey'
            '</a> '
            "to enable Workshop search on the Workshop Mods tab. "
            "Stored in plain text in config.json - optional, "
            "and only used for read-only Workshop lookups."
        )

        api_key_note.setOpenExternalLinks(
            True
        )

        api_key_note.setWordWrap(
            True
        )

        api_key_note.setStyleSheet(
            "color: gray;"
        )

        outer.addWidget(
            api_key_note
        )

        # ========================================================
        # OPTIONAL API FIELDS
        # ========================================================
        #
        # These remain below the main settings because they are
        # service/API credentials rather than connection settings.
        #

        api_form = QFormLayout()

        self.battlemetrics_api_key = QLineEdit(
            str(
                getattr(
                    config,
                    "battlemetrics_api_key",
                    "",
                )
            )
        )

        self.battlemetrics_server_id = QLineEdit(
            str(
                getattr(
                    config,
                    "battlemetrics_server_id",
                    "",
                )
            )
        )

        self.steam_api_key = QLineEdit(
            str(
                getattr(
                    config,
                    "steam_api_key",
                    "",
                )
            )
        )

        self.steam_api_key.setEchoMode(
            QLineEdit.Password
        )

        self.battlemetrics_api_key.setEchoMode(
            QLineEdit.Password
        )

        api_form.addRow(
            "Steam Web API key",
            self.steam_api_key,
        )

        api_form.addRow(
            "BattleMetrics API key",
            self.battlemetrics_api_key,
        )

        api_form.addRow(
            "BattleMetrics server ID",
            self.battlemetrics_server_id,
        )

        outer.addLayout(
            api_form
        )

        # ========================================================
        # SSH KEY BROWSER
        # ========================================================

        browse_row = QHBoxLayout()

        browse_btn = QPushButton(
            "Browse for SSH key locally..."
        )

        browse_btn.clicked.connect(
            self.browse_key
        )

        browse_row.addWidget(
            browse_btn
        )

        outer.addLayout(
            browse_row
        )

        # ========================================================
        # SAVE
        # ========================================================

        save_btn = QPushButton(
            "Save Settings"
        )

        save_btn.clicked.connect(
            self.save
        )

        outer.addWidget(
            save_btn
        )

        outer.addStretch()

    # ============================================================
    # SSH KEY
    # ============================================================

    def browse_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH private key",
        )

        if path:
            self.fields["key_path"].setText(
                path
            )

    # ============================================================
    # SAVE
    # ============================================================

    def save(self):
        # --------------------------------------------------------
        # NORMAL SETTINGS
        # --------------------------------------------------------

        for attr, edit in self.fields.items():
            value = edit.text()

            if attr == "port":
                try:
                    value = int(
                        value
                    )
                except ValueError:
                    value = 22

            setattr(
                self.config,
                attr,
                value,
            )

        # --------------------------------------------------------
        # RCON AUTO RECONNECT
        # --------------------------------------------------------

        self.config.rcon_auto_reconnect = (
            self.rcon_auto_reconnect_checkbox.isChecked()
        )

        # --------------------------------------------------------
        # API SETTINGS
        # --------------------------------------------------------

        self.config.steam_api_key = (
            self.steam_api_key.text()
        )

        self.config.battlemetrics_api_key = (
            self.battlemetrics_api_key.text()
        )

        self.config.battlemetrics_server_id = (
            self.battlemetrics_server_id.text()
        )

        # --------------------------------------------------------
        # SAVE CONFIG
        # --------------------------------------------------------

        self.config.save()

        QMessageBox.information(
            self,
            "Saved",
            "Settings saved.",
        )

        if self.on_saved:
            self.on_saved()
