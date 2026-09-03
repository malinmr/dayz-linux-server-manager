import os
import re
import shlex
import subprocess
import time

from pathlib import (
    Path,
    PurePosixPath,
)

from PySide6.QtCore import (
    QObject,
    Signal,
    Slot,
)

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import AppConfig
from worker import WorkerRegistry


# ============================================================
# LOG BRIDGE
# ============================================================

class MaintenanceLogBridge(QObject):
    """
    Provides a Qt signal that can safely be emitted by the
    background WorkerRegistry worker.

    The callable executed by WorkerRegistry runs on a background
    QThread, while append_log() remains connected to the GUI
    thread.
    """

    message = Signal(str)


# ============================================================
# MAINTENANCE OPERATION
# ============================================================

class MaintenanceOperation:
    """
    Performs the actual remote maintenance operation.

    This class contains no GUI code.

    WorkerRegistry executes run() in the background thread.
    """

    def __init__(
        self,
        ssh,
        config: AppConfig,
        operation,
        backup_destination=None,
        sudo_password_getter=None,
        log_callback=None,
    ):
        self.ssh = ssh
        self.config = config
        self.operation = operation
        self.backup_destination = (
            backup_destination
        )
        self.sudo_password_getter = (
            sudo_password_getter
        )
        self.log_callback = log_callback

    # ========================================================
    # GENERAL HELPERS
    # ========================================================

    def _log(self, message):
        message = str(message)

        if self.log_callback is not None:
            self.log_callback(message)

    def _check_connection(self):
        if not self.ssh.is_connected():
            raise RuntimeError(
                "SSH connection is not active."
            )

    def _service_name(self):
        service = (
            self.config.systemd_service
            or ""
        ).strip()

        if service.endswith(".service"):
            service = service[:-8]

        if not service:
            raise RuntimeError(
                "No systemd service is configured."
            )

        return service

    def _sudo_password(self):
        if self.sudo_password_getter is None:
            return None

        return self.sudo_password_getter()

    def _exec(self, command):
        """
        Execute a normal SSH command using the existing
        SSH connection.
        """

        self._check_connection()

        result = self.ssh.exec(
            command
        )

        return self._normalize_exec_result(
            result
        )

    def _exec_sudo(self, command):
        """
        Execute a privileged command using the existing
        SSH connection.
        """

        self._check_connection()

        password = self._sudo_password()

        result = self.ssh.exec_sudo(
            command,
            password,
        )

        return self._normalize_exec_result(
            result
        )

    @staticmethod
    def _normalize_exec_result(result):
        """
        Normalize common SSH command result formats.

        Supported:

            string

            (stdout, stderr)

            (exit_code, stdout, stderr)

        Returns:

            exit_code,
            stdout,
            stderr
        """

        if isinstance(result, tuple):

            if len(result) == 3:
                first = result[0]

                if isinstance(first, int):
                    return (
                        first,
                        str(result[1] or ""),
                        str(result[2] or ""),
                    )

            if len(result) == 2:
                return (
                    0,
                    str(result[0] or ""),
                    str(result[1] or ""),
                )

        if result is None:
            return (
                0,
                "",
                "",
            )

        return (
            0,
            str(result),
            "",
        )

    def _require_success(
        self,
        result,
        action,
    ):
        exit_code, stdout, stderr = result

        if exit_code != 0:
            details = (
                stderr.strip()
                or stdout.strip()
                or "Unknown error."
            )

            raise RuntimeError(
                f"{action} failed:\n{details}"
            )

        return stdout

    # ========================================================
    # BACKUP
    # ========================================================

    def _validate_backup_config(self):
        """
        Validate the actual SSH configuration stored in
        AppConfig.

        No hostname, username, key, port, or server path is
        guessed here.

        The user must configure the connection in Settings.
        """

        host = str(
            self.config.host
            or ""
        ).strip()

        username = str(
            self.config.username
            or ""
        ).strip()

        key_path = str(
            self.config.key_path
            or ""
        ).strip()

        server_root = str(
            self.config.server_root
            or ""
        ).strip().rstrip("/")

        port = self.config.port

        missing = []

        if not host:
            missing.append(
                "SSH Host"
            )

        if not username:
            missing.append(
                "SSH Username"
            )

        if not key_path:
            missing.append(
                "SSH Private Key"
            )

        if not server_root:
            missing.append(
                "Server Root"
            )

        if missing:
            raise RuntimeError(
                "SSH/server configuration is incomplete.\n\n"
                "Please open the Settings panel and configure:\n\n"
                + "\n".join(
                    f"• {item}"
                    for item in missing
                )
            )

        try:
            port = int(port)

        except (
            TypeError,
            ValueError,
        ):
            raise RuntimeError(
                "The configured SSH port is invalid.\n\n"
                "Please check the SSH settings."
            )

        if port < 1 or port > 65535:
            raise RuntimeError(
                "The configured SSH port must be between "
                "1 and 65535.\n\n"
                "Please check the SSH settings."
            )

        key_file = Path(
            key_path
        ).expanduser()

        if not key_file.exists():
            raise RuntimeError(
                "The configured SSH private key was not found:\n\n"
                f"{key_file}\n\n"
                "Please check the SSH settings."
            )

        if not key_file.is_file():
            raise RuntimeError(
                "The configured SSH private key is not a file:\n\n"
                f"{key_file}\n\n"
                "Please check the SSH settings."
            )

        return (
            host,
            username,
            port,
            str(key_file),
            server_root,
        )

    def _backup(self):
        """
        Back up the configured server root to the selected
        local destination using the user's configured SSH
        connection and rsync.

        Equivalent to:

            rsync -avz --progress \
                -e "ssh -p PORT -i KEY" \
                USER@HOST:/server/root/ \
                /local/destination/
        """

        self._log("")
        self._log("=" * 64)
        self._log("Starting Server Backup")
        self._log("=" * 64)

        # ----------------------------------------------------
        # STEP 1
        # ----------------------------------------------------

        self._log(
            "Step 1/4: Checking the SSH connection..."
        )

        self._check_connection()

        # ----------------------------------------------------
        # STEP 2
        # ----------------------------------------------------

        self._log(
            "Step 2/4: Validating configured SSH settings..."
        )

        (
            host,
            username,
            port,
            key_path,
            server_root,
        ) = self._validate_backup_config()

        destination = str(
            self.backup_destination
            or ""
        ).strip()

        if not destination:
            raise RuntimeError(
                "No backup destination was selected."
            )

        destination_path = Path(
            destination
        ).expanduser()

        if not destination_path.exists():
            destination_path.mkdir(
                parents=True,
                exist_ok=True,
            )

        if not destination_path.is_dir():
            raise RuntimeError(
                "The selected backup destination "
                "is not a directory:\n"
                f"{destination_path}"
            )

        self._log(
            f"SSH host: {host}"
        )

        self._log(
            f"SSH port: {port}"
        )

        self._log(
            f"SSH username: {username}"
        )

        self._log(
            f"SSH key: {key_path}"
        )

        self._log(
            f"Remote source: "
            f"{server_root}/"
        )

        self._log(
            f"Local destination: "
            f"{destination_path}"
        )

        # ----------------------------------------------------
        # STEP 3
        # ----------------------------------------------------

        self._log("")
        self._log(
            "Step 3/4: Running rsync..."
        )

        ssh_command = (
            "ssh "
            f"-p {port} "
            f"-i {shlex.quote(key_path)}"
        )

        source = (
            f"{username}@{host}:"
            f"{server_root}/"
        )

        command = [
            "rsync",
            "-avz",
            "--progress",
            "-e",
            ssh_command,
            source,
            str(destination_path) + os.sep,
        ]

        # Do not print the private key path as part of a shell
        # command that could be copied into a log or terminal.
        self._log(
            "Using rsync over the configured SSH connection."
        )

        self._log(
            f"Source: {source}"
        )

        self._log(
            f"Destination: {destination_path}"
        )

        self._log("")

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

        except FileNotFoundError:
            raise RuntimeError(
                "rsync was not found on this PC.\n\n"
                "Please install rsync and make sure it is "
                "available in your PATH."
            )

        except Exception as error:
            raise RuntimeError(
                "Could not start rsync:\n"
                f"{error}"
            )

        # ----------------------------------------------------
        # STREAM RSYNC OUTPUT
        # ----------------------------------------------------

        try:
            while True:
                line = (
                    process.stdout.readline()
                )

                if line:
                    self._log(
                        line.rstrip(
                            "\r\n"
                        )
                    )

                if (
                    line == ""
                    and process.poll()
                    is not None
                ):
                    break

                time.sleep(0.01)

        finally:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception:
                    pass

        exit_code = process.wait()

        if exit_code != 0:
            raise RuntimeError(
                "rsync failed.\n\n"
                f"Exit code: {exit_code}\n\n"
                "Check the Maintenance Output above for "
                "the rsync error message."
            )

        # ----------------------------------------------------
        # STEP 4
        # ----------------------------------------------------

        self._log("")
        self._log(
            "Step 4/4: Verifying the local destination..."
        )

        if not destination_path.exists():
            raise RuntimeError(
                "The backup destination no longer exists:\n"
                f"{destination_path}"
            )

        self._log(
            f"Backup destination verified: "
            f"{destination_path}"
        )

        self._log("")
        self._log(
            "Server Backup completed successfully."
        )

        return "backup"

    # ========================================================
    # SYSTEMD STATE
    # ========================================================

    def _service_state(self):
        service = self._service_name()

        command = (
            "systemctl is-active "
            f"{shlex.quote(service)}"
        )

        exit_code, stdout, stderr = (
            self._exec(command)
        )

        state = stdout.strip()

        if not state:
            state = "unknown"

        return state

    def _require_stopped(self):
        state = self._service_state()

        self._log(
            f"Current server service state: {state}"
        )

        if state not in {
            "inactive",
            "failed",
        }:
            raise RuntimeError(
                "The DayZ server must be stopped before "
                "this maintenance operation.\n\n"
                f"Current service state: {state}"
            )

    def _wait_for_state(
        self,
        wanted_states,
        timeout=180,
    ):
        wanted_states = set(
            wanted_states
        )

        started = time.monotonic()

        while True:
            state = self._service_state()

            self._log(
                f"Service state: {state}"
            )

            if state in wanted_states:
                return state

            if (
                time.monotonic() - started
                >= timeout
            ):
                raise RuntimeError(
                    "Timed out waiting for systemd service "
                    "to reach: "
                    f"{', '.join(sorted(wanted_states))}\n\n"
                    f"Last state: {state}"
                )

            time.sleep(2)

    # ========================================================
    # REMOTE PATH HELPERS
    # ========================================================

    def _server_root(self):
        root = (
            self.config.server_root
            or ""
        ).strip().rstrip("/")

        if not root:
            raise RuntimeError(
                "Server root is not configured."
            )

        return PurePosixPath(root)

    def _safe_child_path(
        self,
        root,
        relative_path,
    ):
        """
        Resolve relative_path below root.

        Returns None if the path is absolute or would escape
        root through '..'.
        """

        root = PurePosixPath(root)

        relative_path = str(
            relative_path or ""
        ).strip()

        if not relative_path:
            return None

        path = PurePosixPath(
            relative_path
        )

        if path.is_absolute():
            return None

        parts = [
            part
            for part in root.parts
            if part not in ("", "/")
        ]

        for part in path.parts:

            if part in ("", "."):
                continue

            if part == "..":

                if not parts:
                    return None

                parts.pop()

                continue

            parts.append(part)

        root_parts = [
            part
            for part in root.parts
            if part not in ("", "/")
        ]

        if len(parts) < len(root_parts):
            return None

        if parts[:len(root_parts)] != root_parts:
            return None

        return "/" + "/".join(parts)

    # ========================================================
    # SFTP
    # ========================================================

    def _sftp(self):
        self._check_connection()

        return self.ssh.sftp()

    def _read_remote_file(
        self,
        path,
    ):
        sftp = None

        try:
            sftp = self._sftp()

            with sftp.open(
                path,
                "r",
            ) as remote_file:
                return remote_file.read()

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    def _write_remote_file(
        self,
        path,
        content,
    ):
        sftp = None

        try:
            sftp = self._sftp()

            with sftp.open(
                path,
                "w",
            ) as remote_file:
                remote_file.write(content)

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    def _remote_exists(
        self,
        path,
    ):
        sftp = None

        try:
            sftp = self._sftp()

            try:
                sftp.stat(path)
                return True

            except Exception:
                return False

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    def _remote_is_directory(
        self,
        path,
    ):
        sftp = None

        try:
            sftp = self._sftp()

            attributes = sftp.stat(
                path
            )

            return bool(
                attributes.st_mode & 0o040000
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    # ========================================================
    # SERVER CONFIG / ACTIVE MISSION
    # ========================================================

    def _server_config_path(self):
        return str(
            self._server_root()
            / "serverDZ.cfg"
        )

    def _extract_active_mission(
        self,
        config_text,
    ):
        """
        Extract:

            class Missions
            {
                class DayZ
                {
                    template="...";
                };
            }

        from serverDZ.cfg.
        """

        missions_match = re.search(
            r"class\s+Missions\s*\{"
            r"(?P<body>.*?)"
            r"\}",
            config_text,
            re.IGNORECASE
            | re.DOTALL,
        )

        if not missions_match:
            raise RuntimeError(
                "Could not find 'class Missions' "
                "in serverDZ.cfg."
            )

        missions_body = (
            missions_match.group("body")
        )

        dayz_match = re.search(
            r"class\s+DayZ\s*\{"
            r"(?P<body>.*?)"
            r"\}",
            missions_body,
            re.IGNORECASE
            | re.DOTALL,
        )

        if not dayz_match:
            raise RuntimeError(
                "Could not find 'class DayZ' "
                "inside class Missions."
            )

        dayz_body = (
            dayz_match.group("body")
        )

        template_match = re.search(
            r"\btemplate\s*=\s*"
            r'"([^"]+)"',
            dayz_body,
            re.IGNORECASE,
        )

        if not template_match:
            raise RuntimeError(
                "Could not find the active mission "
                "template in serverDZ.cfg."
            )

        mission = (
            template_match.group(1)
            .strip()
        )

        if not mission:
            raise RuntimeError(
                "The active mission template is empty."
            )

        mission_path = PurePosixPath(
            mission
        )

        if mission_path.is_absolute():
            raise RuntimeError(
                "The active mission template is an "
                "absolute path, which is not allowed."
            )

        if any(
            part == ".."
            for part in mission_path.parts
        ):
            raise RuntimeError(
                "The active mission template contains "
                "path traversal."
            )

        if len(mission_path.parts) != 1:
            raise RuntimeError(
                "The active mission template must be "
                "a single mission directory name."
            )

        return mission

    def _get_active_mission(self):
        config_path = (
            self._server_config_path()
        )

        self._log(
            f"Reading server configuration: "
            f"{config_path}"
        )

        content = self._read_remote_file(
            config_path
        )

        mission = (
            self._extract_active_mission(
                content
            )
        )

        self._log(
            f"Active mission: {mission}"
        )

        return mission

    def _mission_root(
        self,
        mission,
    ):
        relative = (
            PurePosixPath("mpmissions")
            / mission
        )

        safe = self._safe_child_path(
            self._server_root(),
            str(relative),
        )

        if safe is None:
            raise RuntimeError(
                "Resolved mission path is outside "
                "the server root."
            )

        return PurePosixPath(safe)

    # ========================================================
    # SOFT WIPE
    # ========================================================

    def _find_dynamic_line(
        self,
        content,
    ):
        """
        Locate the exact expected dynamic line.

        Returns its position and exact original text.
        """

        pattern = re.compile(
            r"^(?P<indent>\s*)"
            r"<dynamic\s+"
            r'init="1"\s+'
            r'load="1"\s+'
            r'respawn="1"\s+'
            r'save="1"\s*/>'
            r"(?P<newline>\r?\n|$)",
            re.MULTILINE,
        )

        match = pattern.search(
            content
        )

        if not match:
            return None

        return {
            "start": match.start(),
            "end": match.end(),
            "text": match.group(0),
            "indent": match.group("indent"),
            "newline": match.group("newline"),
        }

    def _soft_wipe(self):
        self._log("")
        self._log("=" * 64)
        self._log("Starting Soft Wipe")
        self._log("=" * 64)

        economy_path = None
        original_dynamic_line = None
        temporary_change_applied = False

        try:
            # ------------------------------------------------
            # STEP 1
            # ------------------------------------------------

            self._log(
                "Step 1/9: Verifying the DayZ server "
                "is stopped..."
            )

            self._require_stopped()

            self._log(
                "Server is stopped."
            )

            # ------------------------------------------------
            # STEP 2
            # ------------------------------------------------

            self._log(
                "Step 2/9: Reading the active mission..."
            )

            mission = (
                self._get_active_mission()
            )

            mission_root = (
                self._mission_root(
                    mission
                )
            )

            economy_relative = (
                PurePosixPath("mpmissions")
                / mission
                / "db"
                / "economy.xml"
            )

            safe_economy = (
                self._safe_child_path(
                    self._server_root(),
                    str(economy_relative),
                )
            )

            if safe_economy is None:
                raise RuntimeError(
                    "Resolved economy.xml path is outside "
                    "the server root."
                )

            economy_path = PurePosixPath(
                safe_economy
            )

            self._log(
                f"Mission path: {mission_root}"
            )

            self._log(
                f"Economy file: {economy_path}"
            )

            # ------------------------------------------------
            # STEP 3
            # ------------------------------------------------

            self._log(
                "Step 3/9: Checking economy.xml..."
            )

            if not self._remote_exists(
                str(economy_path)
            ):
                raise RuntimeError(
                    "economy.xml was not found:\n"
                    f"{economy_path}"
                )

            original_content = (
                self._read_remote_file(
                    str(economy_path)
                )
            )

            dynamic = (
                self._find_dynamic_line(
                    original_content
                )
            )

            if dynamic is None:
                raise RuntimeError(
                    "Could not find the expected dynamic "
                    "line with load=\"1\" in economy.xml."
                )

            original_dynamic_line = (
                dynamic["text"]
            )

            self._log(
                "Original dynamic line:"
            )

            self._log(
                original_dynamic_line.rstrip(
                    "\r\n"
                )
            )

            # ------------------------------------------------
            # STEP 4
            # ------------------------------------------------

            self._log(
                "Step 4/9: Changing only load=\"1\" "
                "to load=\"0\"..."
            )

            replacement_line = (
                f'{dynamic["indent"]}'
                '<dynamic init="1" load="0" '
                'respawn="1" save="1"/>'
                f'{dynamic["newline"]}'
            )

            modified_content = (
                original_content[
                    :dynamic["start"]
                ]
                + replacement_line
                + original_content[
                    dynamic["end"] :
                ]
            )

            self._write_remote_file(
                str(economy_path),
                modified_content,
            )

            temporary_change_applied = True

            verify_content = (
                self._read_remote_file(
                    str(economy_path)
                )
            )

            if (
                '<dynamic init="1" '
                'load="0" '
                'respawn="1" '
                'save="1"/>'
                not in verify_content
            ):
                raise RuntimeError(
                    "The temporary load=\"0\" change "
                    "could not be verified."
                )

            self._log(
                "Temporary economy.xml change verified."
            )

            # ------------------------------------------------
            # STEP 5
            # ------------------------------------------------

            self._log(
                "Step 5/9: Starting the DayZ server..."
            )

            service = self._service_name()

            start_command = (
                "systemctl start "
                f"{shlex.quote(service)}"
            )

            self._require_success(
                self._exec_sudo(
                    start_command
                ),
                "Starting the DayZ server",
            )

            self._log(
                "Start command sent."
            )

            # ------------------------------------------------
            # STEP 6
            # ------------------------------------------------

            self._log(
                "Step 6/9: Waiting for the server "
                "to actually start..."
            )

            state = self._wait_for_state(
                {"active"},
                timeout=180,
            )

            self._log(
                f"Server reached state: {state}"
            )

            self._log(
                "Allowing DayZ a few seconds to "
                "initialize persistence..."
            )

            time.sleep(5)

            # ------------------------------------------------
            # STEP 7
            # ------------------------------------------------

            self._log(
                "Step 7/9: Shutting the server down again..."
            )

            stop_command = (
                "systemctl stop "
                f"{shlex.quote(service)}"
            )

            self._require_success(
                self._exec_sudo(
                    stop_command
                ),
                "Stopping the DayZ server",
            )

            self._log(
                "Stop command sent."
            )

            self._log(
                "Waiting for the server to fully stop..."
            )

            state = self._wait_for_state(
                {
                    "inactive",
                    "failed",
                },
                timeout=180,
            )

            self._log(
                f"Server reached state: {state}"
            )

            # ------------------------------------------------
            # STEP 8
            # ------------------------------------------------

            self._log(
                "Step 8/9: Restoring the exact "
                "original dynamic line..."
            )

            current_content = (
                self._read_remote_file(
                    str(economy_path)
                )
            )

            restore_dynamic = (
                self._find_dynamic_line(
                    current_content
                )
            )

            if restore_dynamic is None:
                raise RuntimeError(
                    "Could not locate the dynamic line "
                    "while restoring economy.xml."
                )

            restored_content = (
                current_content[
                    :restore_dynamic["start"]
                ]
                + original_dynamic_line
                + current_content[
                    restore_dynamic["end"] :
                ]
            )

            self._write_remote_file(
                str(economy_path),
                restored_content,
            )

            temporary_change_applied = False

            self._log(
                "Original dynamic line written back."
            )

            # ------------------------------------------------
            # STEP 9
            # ------------------------------------------------

            self._log(
                "Step 9/9: Verifying the original "
                "line was restored..."
            )

            final_content = (
                self._read_remote_file(
                    str(economy_path)
                )
            )

            if (
                original_dynamic_line
                not in final_content
            ):
                raise RuntimeError(
                    "The original dynamic line could not "
                    "be verified after restoration."
                )

            if (
                '<dynamic init="1" '
                'load="0" '
                'respawn="1" '
                'save="1"/>'
                in final_content
            ):
                raise RuntimeError(
                    "economy.xml still contains the "
                    'temporary load="0" setting.'
                )

            self._log(
                "Original dynamic line restored "
                "and verified."
            )

            self._log("")
            self._log(
                "Soft Wipe completed successfully."
            )

        except Exception:

            # ------------------------------------------------
            # SAFETY RESTORATION
            # ------------------------------------------------

            if (
                temporary_change_applied
                and economy_path is not None
                and original_dynamic_line is not None
            ):
                self._log("")
                self._log(
                    "ERROR occurred while the temporary "
                    "economy setting was active."
                )

                self._log(
                    "Attempting emergency restoration "
                    "of the original dynamic line..."
                )

                try:
                    current_content = (
                        self._read_remote_file(
                            str(economy_path)
                        )
                    )

                    current_dynamic = (
                        self._find_dynamic_line(
                            current_content
                        )
                    )

                    if current_dynamic is not None:
                        restored_content = (
                            current_content[
                                :current_dynamic["start"]
                            ]
                            + original_dynamic_line
                            + current_content[
                                current_dynamic["end"] :
                            ]
                        )

                        self._write_remote_file(
                            str(economy_path),
                            restored_content,
                        )

                        self._log(
                            "Emergency restoration completed."
                        )

                        temporary_change_applied = False

                    else:
                        self._log(
                            "WARNING: Could not locate the "
                            "dynamic line for emergency "
                            "restoration."
                        )

                except Exception as restore_error:
                    self._log(
                        "WARNING: Emergency restoration "
                        "failed:"
                    )

                    self._log(
                        str(restore_error)
                    )

            raise

    # ========================================================
    # FULL WIPE
    # ========================================================

    def _wipe_marker_to_path(
        self,
        marker,
    ):
        marker = str(
            marker or ""
        ).strip()

        if not marker:
            return None

        path = PurePosixPath(
            marker
        )

        if path.is_absolute():
            return None

        return self._safe_child_path(
            self._server_root(),
            marker,
        )

    def _collect_full_wipe_targets(
        self,
        mission,
    ):
        root = self._server_root()

        storage_relative = (
            PurePosixPath("mpmissions")
            / mission
            / "storage_1"
        )

        storage_path = (
            self._safe_child_path(
                root,
                str(storage_relative),
            )
        )

        if storage_path is None:
            raise RuntimeError(
                "The storage_1 path is outside "
                "the server root."
            )

        targets = [
            {
                "path": storage_path,
                "label": (
                    "Active mission storage_1"
                ),
                "exists": False,
                "type": "directory",
            }
        ]

        markers = getattr(
            self.config,
            "marked_wipe_files",
            [],
        )

        if not isinstance(
            markers,
            list,
        ):
            markers = []

        seen = {
            storage_path
        }

        for marker in markers:

            marker = str(
                marker or ""
            ).strip()

            if not marker:
                continue

            path = (
                self._wipe_marker_to_path(
                    marker
                )
            )

            if path is None:
                raise RuntimeError(
                    "Unsafe wipe marker found in "
                    f"configuration:\n{marker}"
                )

            if path in seen:
                continue

            seen.add(path)

            targets.append({
                "path": path,
                "label": marker,
                "exists": False,
                "type": "file",
            })

        # ----------------------------------------------------
        # Check existence.
        # ----------------------------------------------------

        for target in targets:

            exists = self._remote_exists(
                target["path"]
            )

            target["exists"] = exists

            if exists:
                target["type"] = (
                    "directory"
                    if self._remote_is_directory(
                        target["path"]
                    )
                    else "file"
                )

        return targets

    def _delete_remote_path(
        self,
        path,
    ):
        """
        Recursively delete a remote file or directory using
        the existing SFTP connection.
        """

        sftp = None

        try:
            sftp = self._sftp()

            self._delete_remote_path_with_sftp(
                sftp,
                path,
            )

        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass

    def _delete_remote_path_with_sftp(
        self,
        sftp,
        path,
    ):
        attributes = sftp.stat(
            path
        )

        is_dir = bool(
            attributes.st_mode & 0o040000
        )

        if not is_dir:
            sftp.remove(path)
            return

        for item in sftp.listdir_attr(
            path
        ):
            child = str(
                PurePosixPath(path)
                / item.filename
            )

            child_is_dir = bool(
                item.st_mode & 0o040000
            )

            if child_is_dir:
                self._delete_remote_path_with_sftp(
                    sftp,
                    child,
                )
            else:
                sftp.remove(child)

        sftp.rmdir(path)

    def _full_wipe(self):
        self._log("")
        self._log("=" * 64)
        self._log("Starting Full Wipe")
        self._log("=" * 64)

        # ----------------------------------------------------
        # STEP 1
        # ----------------------------------------------------

        self._log(
            "Step 1/4: Verifying the DayZ server "
            "is stopped..."
        )

        self._require_stopped()

        self._log(
            "Server is stopped."
        )

        # ----------------------------------------------------
        # STEP 2
        # ----------------------------------------------------

        self._log(
            "Step 2/4: Reading the active mission "
            "and building wipe targets..."
        )

        mission = (
            self._get_active_mission()
        )

        targets = (
            self._collect_full_wipe_targets(
                mission
            )
        )

        self._log(
            f"Active mission: {mission}"
        )

        self._log(
            "Wipe targets:"
        )

        for target in targets:
            if target["exists"]:
                state = "EXISTS - WILL DELETE"
            else:
                state = "MISSING - WILL SKIP"

            self._log(
                f"  [{state}] {target['path']}"
            )

        existing_targets = [
            target
            for target in targets
            if target["exists"]
        ]

        self._log(
            f"Existing targets: "
            f"{len(existing_targets)}"
        )

        # ----------------------------------------------------
        # STEP 3
        # ----------------------------------------------------

        self._log(
            "Step 3/4: Performing final service-state "
            "check immediately before deletion..."
        )

        self._require_stopped()

        self._log(
            "Final service-state check passed."
        )

        if not existing_targets:
            self._log(
                "Nothing exists that needs to be deleted."
            )

            self._log(
                "Full Wipe completed."
            )

            return

        # ----------------------------------------------------
        # DELETE
        # ----------------------------------------------------

        for index, target in enumerate(
            existing_targets,
            start=1,
        ):
            self._log(
                f"Deleting target "
                f"{index}/{len(existing_targets)}:"
            )

            self._log(
                f"  {target['path']}"
            )

            self._delete_remote_path(
                target["path"]
            )

            if self._remote_exists(
                target["path"]
            ):
                raise RuntimeError(
                    "Deletion could not be verified:\n"
                    f"{target['path']}"
                )

            self._log(
                "  Deleted and verified."
            )

        # ----------------------------------------------------
        # STEP 4
        # ----------------------------------------------------

        self._log(
            "Step 4/4: Verifying all requested "
            "targets are gone..."
        )

        remaining = []

        for target in existing_targets:

            if self._remote_exists(
                target["path"]
            ):
                remaining.append(
                    target["path"]
                )

        if remaining:
            raise RuntimeError(
                "The following wipe targets still exist:\n\n"
                + "\n".join(remaining)
            )

        self._log(
            "All existing wipe targets were deleted."
        )

        self._log(
            "Full Wipe completed successfully."
        )

    # ========================================================
    # ENTRY POINT
    # ========================================================

    def run(self):
        if self.operation == "backup":
            return self._backup()

        self._check_connection()

        if self.operation == "soft_wipe":
            self._soft_wipe()

        elif self.operation == "full_wipe":
            self._full_wipe()

        else:
            raise RuntimeError(
                "Unknown maintenance operation: "
                f"{self.operation}"
            )

        return self.operation


# ============================================================
# MAINTENANCE PANEL
# ============================================================

class MaintenancePanel(QWidget):
    """
    DayZ server maintenance operations.

    Current operations:

        - Server Backup
        - Soft Wipe
        - Full Wipe

    All background work is managed through the application's
    WorkerRegistry.
    """

    def __init__(
        self,
        ssh,
        config: AppConfig,
        sudo_password_getter=None,
        parent=None,
    ):
        super().__init__(parent)

        self.ssh = ssh
        self.config = config

        self.sudo_password_getter = (
            sudo_password_getter
        )

        # IMPORTANT:
        # WorkerRegistry lives in worker.py.
        #
        # It does not accept a Qt parent.
        self.jobs = WorkerRegistry()

        self.log_bridge = (
            MaintenanceLogBridge()
        )

        self.running = False
        self.connected = (
            self.ssh.is_connected()
        )

        self._build_ui()

        self.log_bridge.message.connect(
            self.append_log
        )

        self._update_ui_state()

    # ========================================================
    # UI
    # ========================================================

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        # ----------------------------------------------------
        # BACKUP
        # ----------------------------------------------------

        backup_group = QGroupBox(
            "Server Backup"
        )

        backup_layout = QVBoxLayout(
            backup_group
        )

        backup_description = QLabel(
            "Back up the complete DayZ server directory "
            "to your PC using rsync over SSH. The SSH "
            "connection and server path are taken directly "
            "from Settings."
        )

        backup_description.setWordWrap(
            True
        )

        backup_layout.addWidget(
            backup_description
        )

        backup_buttons_layout = QHBoxLayout()

        self.backup_button = QPushButton(
            "Backup Server"
        )

        self.backup_button.setToolTip(
            "Choose a local folder and copy the complete "
            "configured DayZ server directory to it using rsync."
        )

        backup_buttons_layout.addWidget(
            self.backup_button
        )

        backup_buttons_layout.addStretch()

        backup_layout.addLayout(
            backup_buttons_layout
        )

        main_layout.addWidget(
            backup_group
        )

        # ----------------------------------------------------
        # WIPES
        # ----------------------------------------------------

        wipe_group = QGroupBox(
            "Wipes"
        )

        wipe_layout = QVBoxLayout(
            wipe_group
        )

        description = QLabel(
            "Destructive server maintenance operations. "
            "The DayZ service must be stopped before "
            "either wipe begins."
        )

        description.setWordWrap(
            True
        )

        wipe_layout.addWidget(
            description
        )

        buttons_layout = QHBoxLayout()

        self.soft_wipe_button = QPushButton(
            "Soft Wipe"
        )

        self.full_wipe_button = QPushButton(
            "Full Wipe"
        )

        self.soft_wipe_button.setToolTip(
            "Temporarily disables dynamic economy loading, "
            "starts DayZ, waits for it to start, stops it, "
            "then restores the original economy.xml line."
        )

        self.full_wipe_button.setToolTip(
            "Deletes the active mission storage_1 folder "
            "and files marked for wiping."
        )

        buttons_layout.addWidget(
            self.soft_wipe_button
        )

        buttons_layout.addWidget(
            self.full_wipe_button
        )

        buttons_layout.addStretch()

        wipe_layout.addLayout(
            buttons_layout
        )

        main_layout.addWidget(
            wipe_group
        )

        # ----------------------------------------------------
        # OUTPUT
        # ----------------------------------------------------

        output_group = QGroupBox(
            "Maintenance Output"
        )

        output_layout = QVBoxLayout(
            output_group
        )

        self.output = QTextEdit()

        self.output.setReadOnly(
            True
        )

        self.output.setLineWrapMode(
            QTextEdit.NoWrap
        )

        output_layout.addWidget(
            self.output
        )

        main_layout.addWidget(
            output_group,
            1,
        )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        self.status_label = QLabel(
            "Ready"
        )

        main_layout.addWidget(
            self.status_label
        )

        # ----------------------------------------------------
        # SIGNALS
        # ----------------------------------------------------

        self.backup_button.clicked.connect(
            self.select_backup_destination
        )

        self.soft_wipe_button.clicked.connect(
            self.confirm_soft_wipe
        )

        self.full_wipe_button.clicked.connect(
            self.confirm_full_wipe
        )

    # ========================================================
    # CONNECTION
    # ========================================================

    def set_connected(
        self,
        connected,
    ):
        self.connected = bool(
            connected
        )

        if not self.connected:
            self.status_label.setText(
                "Not connected"
            )

        elif not self.running:
            self.status_label.setText(
                "Ready"
            )

        self._update_ui_state()

    # ========================================================
    # OUTPUT
    # ========================================================

    @Slot(str)
    def append_log(
        self,
        message,
    ):
        self.output.append(
            str(message)
        )

        scrollbar = (
            self.output.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )

    def clear_output(self):
        self.output.clear()

    # ========================================================
    # BACKUP
    # ========================================================

    def select_backup_destination(self):
        """
        Open a native folder chooser and start the backup
        using the selected local directory.
        """

        if self.running:
            return

        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "SSH connection is not active.",
            )
            return

        # ----------------------------------------------------
        # Validate the saved configuration before opening
        # the folder chooser.
        # ----------------------------------------------------

        missing = []

        if not str(
            self.config.host
            or ""
        ).strip():
            missing.append(
                "SSH Host"
            )

        if not str(
            self.config.username
            or ""
        ).strip():
            missing.append(
                "SSH Username"
            )

        if not str(
            self.config.key_path
            or ""
        ).strip():
            missing.append(
                "SSH Private Key"
            )

        if not str(
            self.config.server_root
            or ""
        ).strip():
            missing.append(
                "Server Root"
            )

        if missing:
            QMessageBox.warning(
                self,
                "SSH Configuration Required",
                (
                    "The server backup cannot start because "
                    "the following settings are missing:\n\n"
                    + "\n".join(
                        f"• {item}"
                        for item in missing
                    )
                    + "\n\n"
                    "Please open the Settings panel and "
                    "configure them first."
                ),
            )
            return

        destination = QFileDialog.getExistingDirectory(
            self,
            "Choose Backup Destination",
            str(
                Path.home()
            ),
        )

        if not destination:
            return

        answer = QMessageBox.question(
            self,
            "Confirm Server Backup",
            (
                "The complete configured DayZ server "
                "directory will be copied to:\n\n"
                f"{destination}\n\n"
                "The backup uses rsync over your configured "
                "SSH connection and may take some time "
                "depending on the size of the server.\n\n"
                "Continue?"
            ),
            QMessageBox.Yes
            | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self.start_backup(
            destination
        )

    def start_backup(
        self,
        destination,
    ):
        if self.running:
            return

        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "SSH connection is not active.",
            )
            return

        destination = str(
            destination or ""
        ).strip()

        if not destination:
            return

        self.clear_output()

        self.running = True

        self.status_label.setText(
            "Running Server Backup..."
        )

        self._update_ui_state()

        self._append_header(
            "Server Backup"
        )

        operation_worker = (
            MaintenanceOperation(
                self.ssh,
                self.config,
                "backup",
                backup_destination=destination,
                sudo_password_getter=(
                    self.sudo_password_getter
                ),
                log_callback=(
                    self.log_bridge.message.emit
                ),
            )
        )

        self.jobs.start(
            operation_worker.run,
            on_ok=self._operation_finished,
            on_fail=self._operation_failed,
        )

    # ========================================================
    # CONFIRMATIONS
    # ========================================================

    def confirm_soft_wipe(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "SSH connection is not active.",
            )
            return

        answer = QMessageBox.warning(
            self,
            "Confirm Soft Wipe",
            (
                "Soft Wipe will:\n\n"
                "1. Verify the DayZ server is stopped.\n"
                "2. Read the active mission from "
                "serverDZ.cfg.\n"
                "3. Locate that mission's "
                "db/economy.xml.\n"
                "4. Temporarily change only the dynamic "
                'economy setting from load="1" to load="0".\n'
                "5. Start the server.\n"
                "6. Wait until systemd reports the server "
                "as active.\n"
                "7. Stop the server again.\n"
                "8. Restore the exact original dynamic line.\n"
                "9. Verify the original configuration.\n\n"
                "The server will be started and stopped "
                "automatically.\n\n"
                "Continue?"
            ),
            QMessageBox.Yes
            | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self.start_operation(
            "soft_wipe"
        )

    def confirm_full_wipe(self):
        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "SSH connection is not active.",
            )
            return

        markers = getattr(
            self.config,
            "marked_wipe_files",
            [],
        )

        if not isinstance(
            markers,
            list,
        ):
            markers = []

        marker_lines = []

        for marker in markers:
            marker = str(
                marker or ""
            ).strip()

            if marker:
                marker_lines.append(
                    f"• {marker}"
                )

        if marker_lines:
            marked_text = "\n".join(
                marker_lines
            )
        else:
            marked_text = (
                "• No additional files are marked "
                "for wiping."
            )

        answer = QMessageBox.warning(
            self,
            "Confirm Full Wipe",
            (
                "Full Wipe will permanently delete:\n\n"
                "• The active mission's storage_1 folder\n"
                "• These files marked for wiping:\n"
                f"{marked_text}\n\n"
                "The active mission is read directly from "
                "serverDZ.cfg when the operation runs.\n\n"
                "The server must be stopped.\n\n"
                "This operation cannot be undone.\n\n"
                "Continue?"
            ),
            QMessageBox.Yes
            | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self.start_operation(
            "full_wipe"
        )

    # ========================================================
    # OPERATIONS
    # ========================================================

    def start_operation(
        self,
        operation,
    ):
        if self.running:
            return

        if not self.ssh.is_connected():
            QMessageBox.warning(
                self,
                "Not Connected",
                "SSH connection is not active.",
            )
            return

        self.clear_output()

        operation_name = (
            "Soft Wipe"
            if operation == "soft_wipe"
            else "Full Wipe"
        )

        self.running = True

        self.status_label.setText(
            f"Running {operation_name}..."
        )

        self._update_ui_state()

        self._append_header(
            operation_name
        )

        # ----------------------------------------------------
        # Log bridge
        # ----------------------------------------------------
        #
        # The callable executes in the WorkerRegistry's
        # background thread.
        #
        # It emits messages through a Qt signal so the GUI
        # receives them safely.

        operation_worker = (
            MaintenanceOperation(
                self.ssh,
                self.config,
                operation,
                sudo_password_getter=(
                    self.sudo_password_getter
                ),
                log_callback=(
                    self.log_bridge.message.emit
                ),
            )
        )

        self.jobs.start(
            operation_worker.run,
            on_ok=self._operation_finished,
            on_fail=self._operation_failed,
        )

    def _append_header(
        self,
        title,
    ):
        self.output.append("")
        self.output.append(
            "=" * 64
        )
        self.output.append(
            title
        )
        self.output.append(
            "=" * 64
        )
        self.output.append("")

    # ========================================================
    # RESULTS
    # ========================================================

    @Slot(object)
    def _operation_finished(
        self,
        operation,
    ):
        if operation == "backup":
            title = "Server Backup"

        elif operation == "soft_wipe":
            title = "Soft Wipe"

        elif operation == "full_wipe":
            title = "Full Wipe"

        else:
            title = str(
                operation
            )

        self.output.append("")
        self.output.append(
            "=" * 64
        )
        self.output.append(
            f"{title} completed successfully."
        )
        self.output.append(
            "=" * 64
        )

        self.status_label.setText(
            f"{title} completed successfully."
        )

        self.running = False

        self._update_ui_state()

    @Slot(str)
    def _operation_failed(
        self,
        message,
    ):
        self.output.append("")
        self.output.append(
            "=" * 64
        )
        self.output.append(
            "MAINTENANCE ERROR"
        )
        self.output.append(
            "=" * 64
        )
        self.output.append(
            str(message)
        )

        self.status_label.setText(
            "Maintenance operation failed."
        )

        self.running = False

        self._update_ui_state()

        QMessageBox.critical(
            self,
            "Maintenance Error",
            str(message),
        )

    # ========================================================
    # UI STATE
    # ========================================================

    def _update_ui_state(self):
        connected = (
            self.connected
            and self.ssh.is_connected()
        )

        enabled = (
            connected
            and not self.running
        )

        self.backup_button.setEnabled(
            enabled
        )

        self.soft_wipe_button.setEnabled(
            enabled
        )

        self.full_wipe_button.setEnabled(
            enabled
        )

    # ========================================================
    # OPTIONAL CONFIG REFRESH
    # ========================================================

    def refresh_config_paths(self):
        """
        Maintenance paths are resolved dynamically from
        AppConfig and serverDZ.cfg.

        This method exists so MainWindow can safely call it
        after Settings changes.
        """

        self._update_ui_state()

    # ========================================================
    # SHUTDOWN
    # ========================================================

    def shutdown(self):
        """
        Compatibility helper for application shutdown.

        MainWindow normally calls:

            self.maintenance_panel.jobs.shutdown()

        directly.

        This method is also safe to call independently.
        """

        self.jobs.shutdown()
