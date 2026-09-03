import sys

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
)

from config import AppConfig
from ssh_manager import SSHManager

from widgets.status_panel import StatusPanel
from widgets.log_viewer_panel import LogViewerPanel
from widgets.files_panel import FilesPanel
from widgets.config_editor_panel import ConfigEditorPanel
from widgets.mods_panel import ModsPanel
from widgets.deploy_panel import DeployPanel
from widgets.settings_panel import SettingsPanel
from widgets.systemd_panel import SystemdPanel
from widgets.rcon_panel import RConPanel
from widgets.maintenance_panel import MaintenancePanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "DayZ Server Manager"
        )

        self.resize(
            1200,
            1100,
        )

        # ====================================================
        # CONFIG / SSH
        # ====================================================

        self.config = AppConfig.load()

        self.ssh = SSHManager(
            self.config
        )

        # ====================================================
        # TABS
        # ====================================================

        self.tabs = QTabWidget()

        # ====================================================
        # PANELS
        # ====================================================

        # The Server Status tab owns the permanent SSH connection.
        # Its sudo password field is also shared with panels that
        # need privileged commands.

        self.status_panel = StatusPanel(
            self.ssh,
            self.config,
            on_connection_changed=self.on_connection_changed,
        )

        # Historical Log Viewer uses the same permanent SSH
        # connection as the rest of the application.

        self.log_viewer_panel = LogViewerPanel(
            self.ssh,
            self.config,
        )

        # Server Files uses the same permanent SSH connection.

        self.files_panel = FilesPanel(
            self.ssh,
            self.config,
        )

        # Config Editor uses the same permanent SSH connection.

        self.config_panel = ConfigEditorPanel(
            self.ssh,
            self.config,
        )

        # Workshop Mods uses the same permanent SSH connection.

        self.mods_panel = ModsPanel(
            self.ssh,
            self.config,
        )

        # ====================================================
        # SUDO PASSWORD GETTER
        # ====================================================
        #
        # The actual sudo password remains in the Server Status
        # panel. Other panels receive only a getter so they can
        # request the current value when needed.
        #
        # The password is never stored in AppConfig.

        sudo_password_getter = (
            lambda: self.status_panel.sudo_password_edit.text()
        )

        # ====================================================
        # DEPLOY
        # ====================================================

        self.deploy_panel = DeployPanel(
            self.ssh,
            self.config,
            sudo_password_getter=sudo_password_getter,
        )

        # ====================================================
        # SYSTEMD
        # ====================================================

        self.systemd_panel = SystemdPanel(
            self.ssh,
            self.config,
            sudo_password_getter=sudo_password_getter,
        )

        # ====================================================
        # MAINTENANCE
        # ====================================================
        #
        # Maintenance operations use the existing permanent SSH
        # connection.
        #
        # The Maintenance Panel currently provides:
        #
        #   - Soft Wipe
        #   - Full Wipe
        #
        # More maintenance operations can be added later without
        # changing the MainWindow architecture.

        self.maintenance_panel = MaintenancePanel(
            self.ssh,
            self.config,
            sudo_password_getter=sudo_password_getter,
        )

        # ====================================================
        # RCON
        # ====================================================
        #
        # RCon uses the existing SSH connection when it needs to
        # inspect or create the remote Linux BattlEye config.
        #
        # The actual BattlEye RCon UDP connection remains
        # independent from the SSH connection.

        self.rcon_panel = RConPanel(
            self.ssh,
            self.config,
        )

        # ====================================================
        # WORKSHOP MODS -> SYSTEMD PANEL
        # ====================================================
        #
        # The Workshop Mods panel generates the -mod= and
        # -servermod= values.
        #
        # It does NOT write the systemd unit itself.
        #
        # The generated values are sent to the Systemd Panel,
        # where the user can inspect/edit them before applying
        # them to ExecStart and saving the unit.

        self.mods_panel.mod_parameters_generated.connect(
            self.systemd_panel.set_mod_parameters
        )

        # ====================================================
        # SYSTEMD PROFILES -> FILES / LOG VIEWER
        # ====================================================
        #
        # The Systemd service is authoritative for the DayZ
        # -profiles= parameter.
        #
        # SystemdPanel emits the resolved profiles directory
        # whenever the -profiles= parameter is loaded or changed.
        #
        # FilesPanel and LogViewerPanel do not access
        # SystemdPanel directly.
        #
        # MainWindow acts as the coordinator between the panels.

        self.systemd_panel.profiles_path_changed.connect(
            self.files_panel.set_profiles_path
        )

        self.systemd_panel.profiles_path_changed.connect(
            self.log_viewer_panel.set_profiles_path
        )

        # Give the Log Viewer the currently configured profiles
        # path immediately.
        #
        # This is only the initial fallback. If SystemdPanel
        # resolves a different -profiles= value, its signal above
        # will update the Log Viewer with the authoritative path.

        configured_profiles_path = getattr(
            self.config,
            "profiles_dir",
            None,
        )

        if configured_profiles_path:
            self.log_viewer_panel.set_profiles_path(
                configured_profiles_path
            )

        # ====================================================
        # SETTINGS
        # ====================================================

        self.settings_panel = SettingsPanel(
            self.config,
            self.on_settings_saved,
        )

        # ====================================================
        # ADD TABS
        # ====================================================

        self.tabs.addTab(
            self.status_panel,
            "Server Status",
        )

        self.tabs.addTab(
            self.log_viewer_panel,
            "Log Viewer",
        )

        self.tabs.addTab(
            self.files_panel,
            "Server Files",
        )

        self.tabs.addTab(
            self.config_panel,
            "Config Editor",
        )

        self.tabs.addTab(
            self.mods_panel,
            "Workshop Mods",
        )

        self.tabs.addTab(
            self.systemd_panel,
            "Systemd Service",
        )

        self.tabs.addTab(
            self.maintenance_panel,
            "Maintenance",
        )

        self.tabs.addTab(
            self.deploy_panel,
            "Deploy",
        )

        self.tabs.addTab(
            self.settings_panel,
            "Settings",
        )

        self.tabs.addTab(
            self.rcon_panel,
            "RCON",
        )

        self.setCentralWidget(
            self.tabs
        )

        # ====================================================
        # CONNECTION-AWARE PANELS
        # ====================================================
        #
        # These panels depend on the permanent SSH connection.
        #
        # RConPanel is intentionally NOT included here because
        # its RCon connection is independent from SSH.
        #
        # MaintenancePanel IS included because it uses the shared
        # SSH connection.

        self._connection_aware_panels = [
            self.log_viewer_panel,
            self.files_panel,
            self.config_panel,
            self.mods_panel,
            self.deploy_panel,
            self.systemd_panel,
            self.maintenance_panel,
        ]

    # ========================================================
    # CONNECTION STATE
    # ========================================================

    def on_connection_changed(self, connected):
        """
        Notify every panel that shares the permanent SSH session
        that the connection state has changed.
        """

        for panel in self._connection_aware_panels:
            panel.set_connected(
                connected
            )

    # ========================================================
    # SETTINGS
    # ========================================================

    def on_settings_saved(self):
        """
        Settings may contain new SSH credentials, connection
        details or RCon configuration.

        Drop the existing SSH session so stale credentials are
        never reused.

        RCon is refreshed separately because it has its own
        connection lifecycle.
        """

        if self.ssh.is_connected():
            self.status_panel.disconnect_ssh()

        # ----------------------------------------------------
        # FILES
        # ----------------------------------------------------

        refresh_files = getattr(
            self.files_panel,
            "refresh_config_paths",
            None,
        )

        if refresh_files is not None:
            refresh_files()

        # ----------------------------------------------------
        # DEPLOY
        # ----------------------------------------------------

        refresh_deploy = getattr(
            self.deploy_panel,
            "refresh_config_paths",
            None,
        )

        if refresh_deploy is not None:
            refresh_deploy()

        # ----------------------------------------------------
        # RCON
        # ----------------------------------------------------

        refresh_rcon = getattr(
            self.rcon_panel,
            "refresh_config",
            None,
        )

        if refresh_rcon is not None:
            refresh_rcon()

        # ----------------------------------------------------
        # MAINTENANCE
        # ----------------------------------------------------
        #
        # Maintenance normally reads server paths dynamically,
        # but allow it to refresh if its implementation provides
        # a refresh_config_paths() method.

        refresh_maintenance = getattr(
            self.maintenance_panel,
            "refresh_config_paths",
            None,
        )

        if refresh_maintenance is not None:
            refresh_maintenance()

    # ========================================================
    # APPLICATION SHUTDOWN
    # ========================================================

    def closeEvent(self, event):
        """
        Shut down all worker registries and the RCon connection
        before destroying the application.
        """

        # ----------------------------------------------------
        # RCON
        # ----------------------------------------------------

        self.rcon_panel.close()

        # ----------------------------------------------------
        # WORKERS
        # ----------------------------------------------------

        for panel in (
            self.status_panel,
            self.log_viewer_panel,
            self.config_panel,
            self.mods_panel,
            self.deploy_panel,
            self.systemd_panel,
            self.maintenance_panel,
            self.rcon_panel,
        ):
            panel.jobs.shutdown()

        # ----------------------------------------------------
        # FILES PANEL
        # ----------------------------------------------------

        # FilesPanel has its own worker/thread lifecycle.
        self.files_panel.cleanup()

        # ----------------------------------------------------
        # SSH
        # ----------------------------------------------------

        # Finally close the shared SSH connection.
        self.ssh.close()

        super().closeEvent(
            event
        )


def main():
    app = QApplication(
        sys.argv
    )

    app.setApplicationName(
        "DayZ Server Manager"
    )

    window = MainWindow()

    window.show()

    sys.exit(
        app.exec()
    )


if __name__ == "__main__":
    main()
