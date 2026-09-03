import shlex

import paramiko


class SSHManager:
    """
    Manages one long-lived Paramiko SSH connection.

    The SSH connection is opened only by an explicit connect() call.

    SFTP channels are created on demand with sftp() and should be closed by
    the caller when finished. The underlying SSH connection remains alive.

    Sudo passwords are kept only in memory for the current application
    session and are never written to disk.
    """

    def __init__(self, config):
        self.config = config

        # Long-lived SSH client.
        self.client = None

        # Session-only sudo password.
        # Never persisted to disk.
        self.sudo_password = ""

    # ==================================================================
    # Connection
    # ==================================================================

    def connect(self):
        """
        Open the SSH connection.

        If an existing connection is present, it is closed first and
        replaced with a fresh connection.
        """

        self.close()

        client = paramiko.SSHClient()

        client.load_system_host_keys()

        # Trust-on-first-use for hosts not already present in known_hosts.
        # Existing known_hosts entries are still checked.
        client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy()
        )

        client.connect(
            hostname=self.config.host,
            port=int(self.config.port),
            username=self.config.username,
            key_filename=self.config.key_path or None,
            timeout=10,
        )

        # Keep the SSH transport alive through NAT/firewall idle timeouts.
        transport = client.get_transport()

        if transport is not None:
            transport.set_keepalive(30)

        self.client = client

    # ==================================================================
    # Connection state
    # ==================================================================

    def is_connected(self):
        """
        Return True if the SSH transport is currently active.
        """

        if self.client is None:
            return False

        transport = self.client.get_transport()

        return (
            transport is not None
            and transport.is_active()
        )

    def _require_connected(self):
        """
        Raise instead of silently reconnecting.

        Connection ownership remains with the Status tab/application
        controller.
        """

        if not self.is_connected():
            raise RuntimeError(
                "Not connected to the server. "
                "Click Connect on the Server Status tab first."
            )

    # ==================================================================
    # SSH command execution
    # ==================================================================

    def exec(self, command, timeout=60):
        """
        Execute a normal command on the server.

        Returns:
            (exit_code, stdout, stderr)
        """

        self._require_connected()

        stdin, stdout, stderr = self.client.exec_command(
            command,
            timeout=timeout,
        )

        exit_code = stdout.channel.recv_exit_status()

        out = stdout.read().decode(
            errors="replace"
        )

        err = stderr.read().decode(
            errors="replace"
        )

        return exit_code, out, err

    # ==================================================================
    # Sudo command execution
    # ==================================================================

    def exec_sudo(
        self,
        command,
        password,
        timeout=60,
    ):
        """
        Execute a command through sudo using the supplied password.

        The complete command is executed inside a root shell.

        This is important for compound commands such as:

            cp ... && rm ... && systemctl daemon-reload

        Without the explicit shell wrapper, shell operators such as &&,
        ;, pipes, redirects, etc. can be interpreted outside the sudo
        command.

        Returns:
            (exit_code, stdout, stderr)
        """

        self._require_connected()

        password = password or ""

        # Execute the entire command inside the sudo shell.
        #
        # Resulting command is conceptually:
        #
        # sudo -S -p '' -- sh -c '...command...'
        #
        # shlex.quote() protects the complete command as one argument.
        shell_command = (
            "sh -c "
            + shlex.quote(command)
        )

        full_cmd = (
            "sudo -S -p '' -- "
            + shell_command
        )

        stdin, stdout, stderr = self.client.exec_command(
            full_cmd,
            timeout=timeout,
        )

        # Supply the sudo password through stdin.
        stdin.write(
            password + "\n"
        )

        stdin.flush()

        exit_code = stdout.channel.recv_exit_status()

        out = stdout.read().decode(
            errors="replace"
        )

        err = stderr.read().decode(
            errors="replace"
        )

        return exit_code, out, err

    # ==================================================================
    # SFTP
    # ==================================================================

    def sftp(self):
        """
        Open and return a new Paramiko SFTPClient.

        The caller owns this SFTP client and should close it when finished.

        Example:

            sftp = ssh.sftp()

            try:
                files = sftp.listdir_attr("/home")
            finally:
                sftp.close()

        The underlying SSH connection remains open.
        """

        self._require_connected()

        return self.client.open_sftp()

    # ==================================================================
    # Close
    # ==================================================================

    def close(self):
        """
        Close the SSH connection.

        Safe to call even when no connection exists.
        """

        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

            self.client = None

    # ==================================================================
    # Service helpers
    # ==================================================================

    def service_status(self, service):
        """
        Get systemd service status.

        This is intentionally a normal command because systemctl status
        generally does not require root privileges.
        """

        return self.exec(
            f"systemctl status "
            f"{shlex.quote(service)} "
            f"--no-pager -l"
        )

    def service_action(
        self,
        service,
        action,
        sudo_password=None,
    ):
        """
        Start/stop/restart/etc. a systemd service.

        If sudo_password is provided, sudo -S is used.

        Otherwise sudo -n is used, allowing an existing NOPASSWD sudo rule.
        """

        base_cmd = (
            f"systemctl "
            f"{shlex.quote(action)} "
            f"{shlex.quote(service)}"
        )

        if sudo_password:
            return self.exec_sudo(
                base_cmd,
                sudo_password,
            )

        return self.exec(
            f"sudo -n {base_cmd}"
        )

    # ==================================================================
    # Log helpers
    # ==================================================================

    def clear_logs(self, log_dir):
        """
        Delete common server log/crash files from one directory.
        """

        cmd = (
            f"find {shlex.quote(log_dir)} "
            f"-maxdepth 1 "
            f"-type f "
            r"\( "
            f"-iname '*.log' "
            f"-o -iname '*.rpt' "
            f"-o -iname '*.mdmp' "
            f"-o -iname '*.adm' "
            r"\) "
            f"-print -delete"
        )

        return self.exec(cmd)

    # ==================================================================
    # Configuration file search
    # ==================================================================

    def find_config_files(
        self,
        root,
        max_depth=6,
    ):
        """
        Find common configuration/text files below root.
        """

        cmd = (
            f"find {shlex.quote(root)} "
            f"-maxdepth {int(max_depth)} "
            f"-type f "
            r"\( "
            f"-iname '*.json' "
            f"-o -iname '*.cfg' "
            f"-o -iname '*.txt' "
            r"\) "
            f"2>/dev/null"
        )

        code, out, err = self.exec(cmd)

        return [
            line.strip()
            for line in out.splitlines()
            if line.strip()
        ]

    # ==================================================================
    # Read remote file
    # ==================================================================

    def read_file(self, path):
        """
        Read a remote UTF-8 text file.

        Returns:
            str
        """

        self._require_connected()

        sftp = self.sftp()

        try:
            with sftp.open(path, "r") as file:
                data = file.read()

            if isinstance(data, bytes):
                return data.decode(
                    "utf-8",
                    errors="replace",
                )

            return data

        finally:
            sftp.close()

    # ==================================================================
    # Write remote file
    # ==================================================================

    def write_file(
        self,
        path,
        content,
        backup=True,
    ):
        """
        Write a UTF-8 text file to the server.

        If backup=True and the file already exists, a .bak copy is created
        before writing the new contents.

        NOTE:
        This writes using the SSH user's SFTP permissions. It does not
        automatically use sudo.
        """

        self._require_connected()

        sftp = self.sftp()

        try:
            if backup:
                try:
                    sftp.stat(path)

                    self.exec(
                        "cp -f "
                        + shlex.quote(path)
                        + " "
                        + shlex.quote(path + ".bak")
                    )

                except FileNotFoundError:
                    pass

            with sftp.open(path, "w") as file:
                file.write(content)

        finally:
            sftp.close()
