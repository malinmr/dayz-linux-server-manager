import socket
import struct
import threading
import time
import zlib


class BattlEyeRConError(Exception):
    """Base exception for BattlEye RCon errors."""


class BattlEyeRConAuthenticationError(BattlEyeRConError):
    """Raised when the RCon password is rejected."""


class BattlEyeRConTimeout(BattlEyeRConError):
    """Raised when the RCon server does not answer."""


class BattlEyeRConClient:
    """
    Small native BattlEye RCon v2 client.

    The UDP receiver runs in its own thread because BattlEye RCon is a
    persistent protocol: server messages can arrive at any time and must
    be acknowledged promptly.

    GUI/network operations are initiated by WorkerRegistry workers in the
    panel. This class itself never touches Qt widgets.
    """

    HEADER = b"BE"
    HEADER_MARKER = 0xFF

    TYPE_LOGIN = 0x00
    TYPE_COMMAND = 0x01
    TYPE_MESSAGE = 0x02

    LOGIN_TIMEOUT = 5.0
    KEEPALIVE_INTERVAL = 30.0
    SOCKET_TIMEOUT = 1.0

    def __init__(
        self,
        host,
        port,
        password,
        on_message=None,
        on_disconnect=None,
        on_error=None,
    ):
        self.host = host
        self.port = int(port)
        self.password = password

        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.on_error = on_error

        self._socket = None

        self._receiver_thread = None
        self._stop_event = threading.Event()

        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._connected = False
        self._manual_disconnect = False

        self._sequence = 0
        self._last_send = 0.0

    # ==============================================================
    # STATE
    # ==============================================================

    @property
    def connected(self):
        with self._state_lock:
            return self._connected

    def _set_connected(self, value):
        with self._state_lock:
            self._connected = value

    # ==============================================================
    # PACKET BUILDING
    # ==============================================================

    @classmethod
    def _make_packet(cls, payload):
        """
        Build:

            BE
            CRC32(payload)
            FF
            payload

        BattlEye specifies the CRC over the bytes following the
        seven-byte header prefix, including FF.
        """

        crc_data = bytes([cls.HEADER_MARKER]) + payload

        checksum = zlib.crc32(crc_data) & 0xFFFFFFFF

        return (
            cls.HEADER
            + struct.pack("<I", checksum)
            + crc_data
        )

    @classmethod
    def _parse_packet(cls, data):
        """
        Return the payload after validating the BattlEye header
        and CRC.

        Raises BattlEyeRConError for malformed packets.
        """

        if len(data) < 8:
            raise BattlEyeRConError(
                "Received malformed BattlEye RCon packet."
            )

        if data[:2] != cls.HEADER:
            raise BattlEyeRConError(
                "Received packet with invalid BattlEye RCon header."
            )

        expected_crc = struct.unpack(
            "<I",
            data[2:6],
        )[0]

        crc_data = data[6:]

        actual_crc = (
            zlib.crc32(crc_data)
            & 0xFFFFFFFF
        )

        if actual_crc != expected_crc:
            raise BattlEyeRConError(
                "Received BattlEye RCon packet with invalid CRC."
            )

        if data[6] != cls.HEADER_MARKER:
            raise BattlEyeRConError(
                "Received BattlEye RCon packet with invalid marker."
            )

        return data[7:]

    # ==============================================================
    # SEND
    # ==============================================================

    def _send_payload(self, payload):
        sock = self._socket

        if sock is None:
            raise BattlEyeRConError(
                "RCon socket is not connected."
            )

        packet = self._make_packet(payload)

        with self._send_lock:
            sock.send(packet)
            self._last_send = time.monotonic()

    # ==============================================================
    # LOGIN
    # ==============================================================

    def connect(self):
        """
        Connect and authenticate with BattlEye RCon.

        This method blocks only inside the WorkerRegistry worker that
        invokes it.
        """

        self.disconnect(
            notify=False
        )

        if not self.host.strip():
            raise BattlEyeRConError(
                "RCon host is empty."
            )

        if not self.password:
            raise BattlEyeRConError(
                "RCon password is empty."
            )

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )

        sock.settimeout(
            self.LOGIN_TIMEOUT
        )

        try:
            sock.connect(
                (
                    self.host.strip(),
                    self.port,
                )
            )

            self._socket = sock
            self._manual_disconnect = False
            self._sequence = 0

            # Login:
            #
            # 0x00 | ASCII password
            #
            password_bytes = self.password.encode(
                "ascii"
            )

            self._send_payload(
                bytes([self.TYPE_LOGIN])
                + password_bytes
            )

            deadline = (
                time.monotonic()
                + self.LOGIN_TIMEOUT
            )

            while time.monotonic() < deadline:
                remaining = max(
                    0.1,
                    deadline - time.monotonic(),
                )

                sock.settimeout(
                    remaining
                )

                try:
                    data = sock.recv(4096)

                except socket.timeout:
                    continue

                payload = self._parse_packet(data)

                if not payload:
                    continue

                packet_type = payload[0]

                if packet_type != self.TYPE_LOGIN:
                    continue

                if len(payload) < 2:
                    continue

                result = payload[1]

                if result == 0x01:
                    self._set_connected(True)

                    sock.settimeout(
                        self.SOCKET_TIMEOUT
                    )

                    self._stop_event.clear()

                    self._receiver_thread = threading.Thread(
                        target=self._receive_loop,
                        name="BattlEye-RCon",
                        daemon=True,
                    )

                    self._receiver_thread.start()

                    return True

                raise BattlEyeRConAuthenticationError(
                    "BattlEye RCon rejected the password."
                )

            raise BattlEyeRConTimeout(
                "Timed out waiting for BattlEye RCon login response."
            )

        except Exception:
            self._close_socket()
            self._set_connected(False)
            raise

    # ==============================================================
    # COMMANDS
    # ==============================================================

    def send_command(self, command):
        """
        Send a BattlEye command.

        Command packet:

            0x01
            sequence
            ASCII command
        """

        if not self.connected:
            raise BattlEyeRConError(
                "Not connected to BattlEye RCon."
            )

        command = command.strip()

        if not command:
            return

        command_bytes = command.encode(
            "ascii"
        )

        sequence = self._sequence

        self._sequence = (
            self._sequence + 1
        ) & 0xFF

        payload = (
            bytes([
                self.TYPE_COMMAND,
                sequence,
            ])
            + command_bytes
        )

        self._send_payload(
            payload
        )

    # ==============================================================
    # SERVER MESSAGE ACK
    # ==============================================================

    def _ack_message(self, sequence):
        """
        Acknowledge an unsolicited server message.

        BattlEye requires:

            0x02 | sequence
        """

        self._send_payload(
            bytes([
                self.TYPE_MESSAGE,
                sequence,
            ])
        )

    # ==============================================================
    # RECEIVE LOOP
    # ==============================================================

    def _receive_loop(self):
        fragments = {}

        try:
            while not self._stop_event.is_set():
                sock = self._socket

                if sock is None:
                    break

                try:
                    data = sock.recv(8192)

                except socket.timeout:
                    self._send_keepalive_if_needed()
                    continue

                except OSError as exc:
                    if not self._stop_event.is_set():
                        self._notify_error(
                            f"RCon socket error: {exc}"
                        )

                    break

                try:
                    payload = self._parse_packet(
                        data
                    )

                except BattlEyeRConError as exc:
                    self._notify_error(
                        str(exc)
                    )
                    continue

                if not payload:
                    continue

                packet_type = payload[0]

                # --------------------------------------------------
                # COMMAND RESPONSE
                # --------------------------------------------------

                if packet_type == self.TYPE_COMMAND:
                    if len(payload) < 2:
                        continue

                    sequence = payload[1]
                    body = payload[2:]

                    # Multiple-packet response:
                    #
                    # 0x00 | total packets | packet index
                    #
                    if len(body) >= 3 and body[0] == 0x00:
                        total = body[1]
                        index = body[2]

                        fragment_key = sequence

                        fragments.setdefault(
                            fragment_key,
                            {
                                "total": total,
                                "parts": {},
                            },
                        )

                        fragments[
                            fragment_key
                        ]["parts"][index] = body[3:]

                        entry = fragments[
                            fragment_key
                        ]

                        if len(
                            entry["parts"]
                        ) >= entry["total"]:
                            result = b"".join(
                                entry["parts"].get(
                                    i,
                                    b"",
                                )
                                for i in range(
                                    entry["total"]
                                )
                            )

                            del fragments[
                                fragment_key
                            ]

                            self._notify_message(
                                result
                            )

                    else:
                        self._notify_message(
                            body
                        )

                # --------------------------------------------------
                # SERVER MESSAGE
                # --------------------------------------------------

                elif packet_type == self.TYPE_MESSAGE:
                    if len(payload) < 2:
                        continue

                    sequence = payload[1]
                    body = payload[2:]

                    try:
                        self._ack_message(
                            sequence
                        )
                    except Exception:
                        pass

                    self._notify_message(
                        body
                    )

                # --------------------------------------------------
                # LOGIN RESPONSE
                # --------------------------------------------------

                elif packet_type == self.TYPE_LOGIN:
                    # Login responses normally arrive during connect().
                    # Ignore unexpected ones once connected.
                    continue

                self._send_keepalive_if_needed()

        finally:
            was_connected = self.connected

            self._set_connected(False)

            self._close_socket()

            if (
                was_connected
                and not self._manual_disconnect
            ):
                self._notify_disconnect()

    # ==============================================================
    # KEEPALIVE
    # ==============================================================

    def _send_keepalive_if_needed(self):
        if not self.connected:
            return

        if (
            time.monotonic()
            - self._last_send
            >= self.KEEPALIVE_INTERVAL
        ):
            try:
                # Empty command packet:
                #
                # 0x01 | sequence
                #
                # This is the BattlEye keepalive.
                sequence = self._sequence

                self._sequence = (
                    self._sequence + 1
                ) & 0xFF

                self._send_payload(
                    bytes([
                        self.TYPE_COMMAND,
                        sequence,
                    ])
                )

            except Exception as exc:
                self._notify_error(
                    f"RCon keepalive failed: {exc}"
                )

    # ==============================================================
    # DISCONNECT
    # ==============================================================

    def disconnect(self, notify=True):
        self._manual_disconnect = True

        self._stop_event.set()

        self._set_connected(False)

        self._close_socket()

        thread = self._receiver_thread

        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(
                timeout=2.0
            )

        self._receiver_thread = None

        if notify:
            self._notify_disconnect()

    def _close_socket(self):
        sock = self._socket

        self._socket = None

        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    # ==============================================================
    # CALLBACKS
    # ==============================================================

    def _notify_message(self, data):
        if self.on_message is None:
            return

        try:
            text = data.decode(
                "ascii",
                errors="replace",
            )
        except Exception:
            text = repr(data)

        try:
            self.on_message(
                text
            )
        except Exception:
            pass

    def _notify_disconnect(self):
        if self.on_disconnect is None:
            return

        try:
            self.on_disconnect()
        except Exception:
            pass

    def _notify_error(self, message):
        if self.on_error is None:
            return

        try:
            self.on_error(
                message
            )
        except Exception:
            pass
