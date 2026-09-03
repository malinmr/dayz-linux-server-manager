import re
import shlex
import time

from pathlib import PurePosixPath

from PySide6.QtCore import (
    QObject,
    Signal,
    Slot,
)

from PySide6.QtWidgets import (
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
        sudo_password_getter=None,
        log_callback=None,
    ):
        self.ssh = ssh
        self.config = config
        self.operation = operation
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
                self.sudo_password_getter,
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
        if operation == "soft_wipe":
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
