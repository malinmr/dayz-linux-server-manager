from pathlib import PurePosixPath
import shlex
from dataclasses import dataclass
import json
import re
import xml.etree.ElementTree as ET

from PySide6.QtCore import (
    Qt,
    Signal,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QSyntaxHighlighter,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from worker import WorkerRegistry


# ================================================================
# Validation
# ================================================================


@dataclass
class ValidationIssue:
    """
    Represents one validation problem.

    severity:
        error
        warning
        hint
    """

    severity: str
    line: int
    column: int
    message: str
    hint: str = ""

    @property
    def severity_label(self):
        return {
            "error": "Error",
            "warning": "Warning",
            "hint": "Hint",
        }.get(
            self.severity,
            "Issue",
        )


class ConfigValidator:
    """
    Validation for JSON, XML and known DayZ configuration files.
    """

    # --------------------------------------------------------------
    # Public
    # --------------------------------------------------------------

    def validate(
        self,
        path,
        content,
    ):
        path = str(path or "")
        content = content or ""

        extension = PurePosixPath(
            path
        ).suffix.lower()

        issues = []

        # ----------------------------------------------------------
        # JSON
        # ----------------------------------------------------------

        if extension == ".json":
            syntax_ok = self._validate_json(
                content,
                issues,
            )

            if syntax_ok:
                issues.extend(
                    self._validate_dayz_json(
                        path,
                        content,
                    )
                )

            return issues

        # ----------------------------------------------------------
        # XML
        # ----------------------------------------------------------

        if extension == ".xml":
            syntax_ok, root = self._validate_xml(
                content,
                issues,
            )

            if syntax_ok and root is not None:
                issues.extend(
                    self._validate_dayz_xml(
                        path,
                        root,
                        content,
                    )
                )

            return issues

        return issues

    # --------------------------------------------------------------
    # JSON syntax
    # --------------------------------------------------------------

    def _validate_json(
        self,
        content,
        issues,
    ):
        try:
            json.loads(
                content
            )

            return True

        except json.JSONDecodeError as error:
            issues.append(
                ValidationIssue(
                    severity="error",
                    line=error.lineno,
                    column=error.colno,
                    message="JSON syntax error",
                    hint=error.msg,
                )
            )

            return False

    # --------------------------------------------------------------
    # XML syntax
    # --------------------------------------------------------------

    def _validate_xml(
        self,
        content,
        issues,
    ):
        try:
            root = ET.fromstring(
                content
            )

            return True, root

        except ET.ParseError as error:
            line = 1
            column = 1
            message = str(error)

            position = getattr(
                error,
                "position",
                None,
            )

            if (
                isinstance(
                    position,
                    tuple,
                )
                and len(position) >= 2
            ):
                line = position[0]
                column = position[1] + 1

            elif len(error.args) > 1:
                position = error.args[1]

                if isinstance(
                    position,
                    tuple,
                ):
                    line = position[0]
                    column = position[1] + 1

            issues.append(
                ValidationIssue(
                    severity="error",
                    line=line,
                    column=column,
                    message="XML syntax error",
                    hint=message,
                )
            )

            return False, None

    # ==============================================================
    # DayZ JSON
    # ==============================================================

    def _validate_dayz_json(
        self,
        path,
        content,
    ):
        issues = []

        filename = PurePosixPath(
            path
        ).name.lower()

        try:
            data = json.loads(
                content
            )
        except Exception:
            return issues

        if filename == "cfggameplay.json":
            issues.extend(
                self._validate_cfg_gameplay_json(
                    data,
                    content,
                )
            )

        return issues

    def _validate_cfg_gameplay_json(
        self,
        data,
        content,
    ):
        issues = []

        if not isinstance(
            data,
            dict,
        ):
            issues.append(
                ValidationIssue(
                    severity="error",
                    line=1,
                    column=1,
                    message="DayZ configuration error",
                    hint=(
                        "cfggameplay.json should contain "
                        "a JSON object."
                    ),
                )
            )

            return issues

        def walk(
            value,
            path_parts=None,
        ):
            if path_parts is None:
                path_parts = []

            if isinstance(
                value,
                dict,
            ):
                for key, child in value.items():
                    child_path = (
                        path_parts
                        + [str(key)]
                    )

                    if isinstance(
                        child,
                        (int, float),
                    ):
                        if key.lower() in {
                            "min",
                            "minimum",
                        } and child < 0:
                            issues.append(
                                ValidationIssue(
                                    severity="warning",
                                    line=self._find_json_key_line(
                                        content,
                                        key,
                                    ),
                                    column=1,
                                    message=(
                                        f"Suspicious negative "
                                        f"value for '{key}'"
                                    ),
                                    hint=(
                                        "DayZ configuration values "
                                        "such as minimum counts "
                                        "normally should not be negative."
                                    ),
                                )
                            )

                    walk(
                        child,
                        child_path,
                    )

            elif isinstance(
                value,
                list,
            ):
                for child in value:
                    walk(
                        child,
                        path_parts,
                    )

        walk(
            data
        )

        return issues

    # ==============================================================
    # DayZ XML
    # ==============================================================

    def _validate_dayz_xml(
        self,
        path,
        root,
        content,
    ):
        issues = []

        filename = PurePosixPath(
            path
        ).name.lower()

        if filename == "types.xml":
            issues.extend(
                self._validate_types_xml(
                    root,
                    content,
                )
            )

        elif filename == "events.xml":
            issues.extend(
                self._validate_events_xml(
                    root,
                    content,
                )
            )

            issues.extend(
                self._validate_spawn_numbers(
                    root,
                    content,
                )
            )

        elif filename == "globals.xml":
            issues.extend(
                self._validate_globals_xml(
                    root,
                    content,
                )
            )

        elif filename == "spawnabletypes.xml":
            issues.extend(
                self._validate_spawnabletypes_xml(
                    root,
                    content,
                )
            )

        return issues

    # ==============================================================
    # types.xml
    # ==============================================================

    def _validate_types_xml(
        self,
        root,
        content,
    ):
        issues = []

        type_nodes = [
            node
            for node in root.iter()
            if self._local_name(node.tag).lower() == "type"
        ]

        if not type_nodes:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    line=1,
                    column=1,
                    message="DayZ warning: no <type> entries found",
                    hint=(
                        "types.xml normally contains one or more "
                        '<type name="..."> entries.'
                    ),
                )
            )

        seen_names = set()

        for node in type_nodes:
            name = (
                node.attrib.get(
                    "name"
                )
                or ""
            ).strip()

            line = self._find_type_line(
                content,
                name,
            )

            if not name:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        line=line,
                        column=1,
                        message="DayZ error: <type> has no name",
                        hint=(
                            "A types.xml entry should normally have "
                            'a name attribute, for example '
                            '<type name="AKM">.'
                        ),
                    )
                )

            else:
                name_key = name.lower()

                if name_key in seen_names:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            line=line,
                            column=1,
                            message=(
                                f"Duplicate DayZ type '{name}'"
                            ),
                            hint=(
                                "The same type appears more than once "
                                "in this types.xml. Check whether this "
                                "is intentional."
                            ),
                        )
                    )

                seen_names.add(
                    name_key
                )

            # ------------------------------------------------------
            # Validate THIS type's direct nominal/min/max children.
            # ------------------------------------------------------

            values = {}

            for child in list(node):
                child_name = self._local_name(
                    child.tag
                ).lower()

                if child_name not in {
                    "nominal",
                    "min",
                    "max",
                }:
                    continue

                raw_value = (
                    child.text or ""
                ).strip()

                if not raw_value:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            line=self._find_child_line(
                                content,
                                name,
                                child_name,
                            ),
                            column=1,
                            message=(
                                f"DayZ error: "
                                f"<{child_name}> is empty"
                            ),
                            hint=(
                                f"{name or 'This type'}: "
                                f"<{child_name}> must contain "
                                "a numeric value."
                            ),
                        )
                    )

                    continue

                try:
                    number = float(
                        raw_value
                    )

                except ValueError:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            line=self._find_child_line(
                                content,
                                name,
                                child_name,
                            ),
                            column=1,
                            message=(
                                f"DayZ error: "
                                f"<{child_name}> is not numeric"
                            ),
                            hint=(
                                f"{name or 'This type'}: "
                                f"<{child_name}> contains "
                                f"'{raw_value}', but DayZ expects "
                                "a numeric value."
                            ),
                        )
                    )

                    continue

                if child_name not in values:
                    values[child_name] = (
                        number,
                        child,
                    )

            nominal_data = values.get(
                "nominal"
            )

            min_data = values.get(
                "min"
            )

            max_data = values.get(
                "max"
            )

            entry_name = name or "type"

            # ------------------------------------------------------
            # min > max
            # ------------------------------------------------------

            if (
                min_data is not None
                and max_data is not None
                and min_data[0] > max_data[0]
            ):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        line=self._find_child_line(
                            content,
                            name,
                            "min",
                        ),
                        column=1,
                        message=(
                            "DayZ error: "
                            "min is greater than max"
                        ),
                        hint=(
                            f"{entry_name}: "
                            f"min={min_data[0]:g}, "
                            f"max={max_data[0]:g}. "
                            "min must not be greater than max."
                        ),
                    )
                )

            # ------------------------------------------------------
            # min > nominal
            #
            # A nominal value of 0 is treated as deliberately
            # disabled spawning, so this warning is ignored.
            # ------------------------------------------------------

            if (
                min_data is not None
                and nominal_data is not None
                and nominal_data[0] != 0
                and min_data[0] > nominal_data[0]
            ):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        line=self._find_child_line(
                            content,
                            name,
                            "min",
                        ),
                        column=1,
                        message=(
                            "DayZ warning: "
                            "min is greater than nominal"
                        ),
                        hint=(
                            f"{entry_name}: "
                            f"min={min_data[0]:g}, "
                            f"nominal={nominal_data[0]:g}. "
                            "This is suspicious and probably "
                            "isn't what you intended."
                        ),
                    )
                )

            # ------------------------------------------------------
            # nominal > max
            # ------------------------------------------------------

            if (
                nominal_data is not None
                and max_data is not None
                and nominal_data[0] > max_data[0]
            ):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        line=self._find_child_line(
                            content,
                            name,
                            "nominal",
                        ),
                        column=1,
                        message=(
                            "DayZ warning: "
                            "nominal is greater than max"
                        ),
                        hint=(
                            f"{entry_name}: "
                            f"nominal={nominal_data[0]:g}, "
                            f"max={max_data[0]:g}. "
                            "Usually nominal should be less than "
                            "or equal to max."
                        ),
                    )
                )

            # ------------------------------------------------------
            # Negative values
            # ------------------------------------------------------

            for value_name, data in values.items():
                number, child = data

                if number < 0:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            line=self._find_child_line(
                                content,
                                name,
                                value_name,
                            ),
                            column=1,
                            message=(
                                f"DayZ error: "
                                f"<{value_name}> cannot be negative"
                            ),
                            hint=(
                                f"{entry_name}: "
                                f"<{value_name}> is {number:g}. "
                                "DayZ spawn counts cannot be negative."
                            ),
                        )
                    )

        return issues

    # ==============================================================
    # events.xml
    # ==============================================================

    def _validate_events_xml(
        self,
        root,
        content,
    ):
        issues = []

        event_nodes = [
            node
            for node in root.iter()
            if self._local_name(node.tag).lower() == "event"
        ]

        seen_names = set()

        for node in event_nodes:
            name = (
                node.attrib.get(
                    "name"
                )
                or ""
            ).strip()

            line = self._find_tag_line(
                content,
                "event",
                name,
            )

            if not name:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        line=line,
                        column=1,
                        message=(
                            "DayZ warning: <event> has no name"
                        ),
                        hint=(
                            "Check that this is a valid DayZ event "
                            "definition."
                        ),
                    )
                )

                continue

            name_key = name.lower()

            if name_key in seen_names:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        line=line,
                        column=1,
                        message=(
                            f"Duplicate DayZ event '{name}'"
                        ),
                        hint=(
                            "This event name appears more than once."
                        ),
                    )
                )

            seen_names.add(
                name_key
            )

        return issues

    # ==============================================================
    # globals.xml
    # ==============================================================

    def _validate_globals_xml(
        self,
        root,
        content,
    ):
        issues = []

        for node in root.iter():
            tag = self._local_name(
                node.tag
            )

            if tag.lower() in {
                "var",
                "value",
                "global",
            }:
                value = (
                    node.text or ""
                ).strip()

                if value:
                    try:
                        float(value)

                    except ValueError:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                line=self._find_element_line(
                                    content,
                                    tag,
                                    value,
                                ),
                                column=1,
                                message=(
                                    f"DayZ warning: '{value}' "
                                    f"is not numeric"
                                ),
                                hint=(
                                    "This looks like a global numeric "
                                    "configuration value."
                                ),
                            )
                        )

        return issues

    # ==============================================================
    # spawnabletypes.xml
    # ==============================================================

    def _validate_spawnabletypes_xml(
        self,
        root,
        content,
    ):
        issues = []

        for node in root.iter():
            if (
                self._local_name(
                    node.tag
                ).lower()
                != "type"
            ):
                continue

            name = (
                node.attrib.get(
                    "name"
                )
                or ""
            ).strip()

            if not name:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        line=self._find_element_line(
                            content,
                            "type",
                            "",
                        ),
                        column=1,
                        message=(
                            "DayZ warning: spawnable <type> "
                            "has no name"
                        ),
                        hint=(
                            "Check the item type name and make sure "
                            "it matches a valid DayZ item."
                        ),
                    )
                )

        return issues

    # ==============================================================
    # Generic spawn/economy numeric validation
    # ==============================================================

    def _validate_spawn_numbers(
        self,
        root,
        content,
    ):
        issues = []

        entry_nodes = [
            node
            for node in root.iter()
            if self._local_name(
                node.tag
            ).lower()
            in {
                "type",
                "event",
            }
        ]

        for parent in entry_nodes:
            children = {}

            entry_tag = self._local_name(
                parent.tag
            ).lower()

            entry_name = (
                parent.attrib.get(
                    "name"
                )
                or ""
            ).strip()

            if not entry_name:
                entry_name = entry_tag

            # ------------------------------------------------------
            # ONLY inspect direct children.
            # ------------------------------------------------------

            for child in list(parent):
                name = self._local_name(
                    child.tag
                ).lower()

                if name not in {
                    "nominal",
                    "min",
                    "max",
                }:
                    continue

                value = (
                    child.text or ""
                ).strip()

                if not value:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            line=self._find_element_line(
                                content,
                                name,
                                value,
                            ),
                            column=1,
                            message=(
                                f"DayZ error: <{name}> "
                                f"is empty"
                            ),
                            hint=(
                                f"<{name}> should contain a "
                                "numeric value."
                            ),
                        )
                    )

                    continue

                try:
                    number = float(
                        value
                    )

                except ValueError:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            line=self._find_element_line(
                                content,
                                name,
                                value,
                            ),
                            column=1,
                            message=(
                                f"DayZ error: <{name}> "
                                f"contains '{value}'"
                            ),
                            hint=(
                                f"<{name}> should contain a "
                                "numeric value."
                            ),
                        )
                    )

                    continue

                if name not in children:
                    children[name] = (
                        number,
                        child,
                    )

            if not children:
                continue

            nominal_data = children.get(
                "nominal"
            )

            min_data = children.get(
                "min"
            )

            max_data = children.get(
                "max"
            )

            # ------------------------------------------------------
            # min > max
            # ------------------------------------------------------

            if (
                min_data is not None
                and max_data is not None
                and min_data[0] > max_data[0]
            ):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        line=self._find_element_line(
                            content,
                            "min",
                            min_data[1].text,
                        ),
                        column=1,
                        message=(
                            "DayZ error: min is greater than max"
                        ),
                        hint=(
                            f"{entry_name}: "
                            f"min={min_data[0]:g}, "
                            f"max={max_data[0]:g}. "
                            "That range is invalid and is not "
                            "going to behave as intended."
                        ),
                    )
                )

            # ------------------------------------------------------
            # min > nominal
            # ------------------------------------------------------

            if (
                min_data is not None
                and nominal_data is not None
                and min_data[0] > nominal_data[0]
            ):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        line=self._find_element_line(
                            content,
                            "min",
                            min_data[1].text,
                        ),
                        column=1,
                        message=(
                            "DayZ warning: "
                            "min is greater than nominal"
                        ),
                        hint=(
                            f"{entry_name}: "
                            f"min={min_data[0]:g}, "
                            f"nominal={nominal_data[0]:g}. "
                            "This is suspicious and probably "
                            "isn't what you intended."
                        ),
                    )
                )

            # ------------------------------------------------------
            # nominal > max
            # ------------------------------------------------------

            if (
                nominal_data is not None
                and max_data is not None
                and nominal_data[0] > max_data[0]
            ):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        line=self._find_element_line(
                            content,
                            "nominal",
                            nominal_data[1].text,
                        ),
                        column=1,
                        message=(
                            "DayZ warning: "
                            "nominal is greater than max"
                        ),
                        hint=(
                            f"{entry_name}: "
                            f"nominal={nominal_data[0]:g}, "
                            f"max={max_data[0]:g}. "
                            "Check the values because the "
                            "economy may not behave as expected."
                        ),
                    )
                )

            # ------------------------------------------------------
            # Negative values
            # ------------------------------------------------------

            for name, data in children.items():
                number, node = data

                if number < 0:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            line=self._find_element_line(
                                content,
                                name,
                                node.text,
                            ),
                            column=1,
                            message=(
                                f"DayZ warning: <{name}> "
                                "is negative"
                            ),
                            hint=(
                                "Negative economy counts normally "
                                "do not make sense here."
                            ),
                        )
                    )

        return issues

    # ==============================================================
    # Helpers
    # ==============================================================

    @staticmethod
    def _local_name(
        tag,
    ):
        if "}" in tag:
            return tag.rsplit(
                "}",
                1,
            )[-1]

        return str(
            tag
        )

    @staticmethod
    def _find_json_key_line(
        content,
        key,
    ):
        pattern = re.compile(
            rf'"{re.escape(key)}"\s*:'
        )

        match = pattern.search(
            content
        )

        if not match:
            return 1

        return (
            content.count(
                "\n",
                0,
                match.start(),
            )
            + 1
        )

    @staticmethod
    def _find_tag_line(
        content,
        tag,
        value=None,
    ):
        if value:
            pattern = re.compile(
                rf"<{re.escape(tag)}\b[^>]*"
                rf'\bname\s*=\s*["\']'
                rf"{re.escape(str(value))}"
                rf'["\']',
                re.IGNORECASE,
            )

        else:
            pattern = re.compile(
                rf"<{re.escape(tag)}\b",
                re.IGNORECASE,
            )

        match = pattern.search(
            content
        )

        if not match:
            return 1

        return (
            content.count(
                "\n",
                0,
                match.start(),
            )
            + 1
        )

    @staticmethod
    def _find_type_line(
        content,
        type_name,
    ):
        if not content:
            return 1

        if type_name:
            pattern = re.compile(
                rf"<(?:[\w.-]+:)?type\b[^>]*"
                rf"\bname\s*=\s*"
                rf"['\"]{re.escape(str(type_name))}['\"]",
                re.IGNORECASE,
            )

            for line_number, line in enumerate(
                content.splitlines(),
                start=1,
            ):
                if pattern.search(
                    line
                ):
                    return line_number

        return ConfigValidator._find_tag_line(
            content,
            "type",
        )

    @staticmethod
    def _find_child_line(
        content,
        type_name,
        child_name,
    ):
        if not content:
            return 1

        lines = content.splitlines()

        type_pattern = re.compile(
            rf"<(?:[\w.-]+:)?type\b[^>]*"
            rf"\bname\s*=\s*"
            rf"['\"]{re.escape(str(type_name or ''))}['\"]",
            re.IGNORECASE,
        )

        child_pattern = re.compile(
            rf"<(?:[\w.-]+:)?"
            rf"{re.escape(str(child_name))}"
            rf"\b",
            re.IGNORECASE,
        )

        inside_type = False
        type_depth = 0

        for index, line in enumerate(
            lines
        ):
            if not inside_type:
                if not type_pattern.search(
                    line
                ):
                    continue

                inside_type = True

                opening = len(
                    re.findall(
                        r"<(?:[\w.-]+:)?type\b",
                        line,
                        re.IGNORECASE,
                    )
                )

                closing = len(
                    re.findall(
                        r"</(?:[\w.-]+:)?type\s*>",
                        line,
                        re.IGNORECASE,
                    )
                )

                type_depth = (
                    opening
                    - closing
                )

                if child_pattern.search(
                    line
                ):
                    return index + 1

                if type_depth <= 0:
                    inside_type = False
                    type_depth = 0

                continue

            if child_pattern.search(
                line
            ):
                return index + 1

            opening = len(
                re.findall(
                    r"<(?:[\w.-]+:)?type\b",
                    line,
                    re.IGNORECASE,
                )
            )

            closing = len(
                re.findall(
                    r"</(?:[\w.-]+:)?type\s*>",
                    line,
                    re.IGNORECASE,
                )
            )

            type_depth += (
                opening
                - closing
            )

            if type_depth <= 0:
                inside_type = False
                type_depth = 0

        return ConfigValidator._find_tag_line(
            content,
            child_name,
        )

    @staticmethod
    def _find_element_line(
        content,
        tag,
        value=None,
    ):
        return ConfigValidator._find_tag_line(
            content,
            tag,
        )


# ================================================================
# Syntax Highlighter
# ================================================================


class ConfigSyntaxHighlighter(QSyntaxHighlighter):
    """
    Lightweight syntax highlighting for:

        .json
        .cfg
        .txt
        .xml
        .c
    """

    def __init__(
        self,
        document,
    ):
        super().__init__(
            document
        )

        self.file_type = ""

        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(
            QColor("#6A9955")
        )

        self.string_format = QTextCharFormat()
        self.string_format.setForeground(
            QColor("#CE9178")
        )

        self.number_format = QTextCharFormat()
        self.number_format.setForeground(
            QColor("#B5CEA8")
        )

        self.boolean_format = QTextCharFormat()
        self.boolean_format.setForeground(
            QColor("#569CD6")
        )

        self.boolean_format.setFontWeight(
            QFont.Weight.Bold
        )

        self.null_format = QTextCharFormat()
        self.null_format.setForeground(
            QColor("#569CD6")
        )

        self.key_format = QTextCharFormat()
        self.key_format.setForeground(
            QColor("#9CDCFE")
        )

        self.section_format = QTextCharFormat()
        self.section_format.setForeground(
            QColor("#DCDCAA")
        )

        self.section_format.setFontWeight(
            QFont.Weight.Bold
        )

        self.operator_format = QTextCharFormat()
        self.operator_format.setForeground(
            QColor("#D4D4D4")
        )

        self.xml_tag_format = QTextCharFormat()
        self.xml_tag_format.setForeground(
            QColor("#569CD6")
        )

        self.xml_tag_format.setFontWeight(
            QFont.Weight.Bold
        )

        self.xml_attribute_format = QTextCharFormat()
        self.xml_attribute_format.setForeground(
            QColor("#9CDCFE")
        )

        self.xml_string_format = QTextCharFormat()
        self.xml_string_format.setForeground(
            QColor("#CE9178")
        )

        self.xml_entity_format = QTextCharFormat()
        self.xml_entity_format.setForeground(
            QColor("#D7BA7D")
        )

        self.xml_declaration_format = QTextCharFormat()
        self.xml_declaration_format.setForeground(
            QColor("#C586C0")
        )

        self.xml_declaration_format.setFontWeight(
            QFont.Weight.Bold
        )

        # ----------------------------------------------------------
        # C syntax highlighting formats
        # ----------------------------------------------------------

        self.c_keyword_format = QTextCharFormat()
        self.c_keyword_format.setForeground(
            QColor("#569CD6")
        )

        self.c_keyword_format.setFontWeight(
            QFont.Weight.Bold
        )

        self.c_type_format = QTextCharFormat()
        self.c_type_format.setForeground(
            QColor("#4EC9B0")
        )

        self.c_preprocessor_format = QTextCharFormat()
        self.c_preprocessor_format.setForeground(
            QColor("#C586C0")
        )

        self.c_function_format = QTextCharFormat()
        self.c_function_format.setForeground(
            QColor("#DCDCAA")
        )

        self.c_char_format = QTextCharFormat()
        self.c_char_format.setForeground(
            QColor("#D7BA7D")
        )

        self.c_number_format = QTextCharFormat()
        self.c_number_format.setForeground(
            QColor("#B5CEA8")
        )

        self.c_operator_format = QTextCharFormat()
        self.c_operator_format.setForeground(
            QColor("#D4D4D4")
        )

        self.c_keyword_pattern = re.compile(
            r"\b(?:"
            r"auto|break|case|const|continue|default|do|else|"
            r"enum|extern|for|goto|if|inline|register|restrict|"
            r"return|sizeof|static|struct|switch|typedef|union|"
            r"volatile|while|_Alignas|_Alignof|_Atomic|_Bool|"
            r"_Complex|_Generic|_Imaginary|_Noreturn|_Static_assert|"
            r"_Thread_local"
            r")\b"
        )

        self.c_type_pattern = re.compile(
            r"\b(?:"
            r"void|char|short|int|long|float|double|signed|"
            r"unsigned|size_t|ptrdiff_t|FILE"
            r")\b"
        )

        self.c_preprocessor_pattern = re.compile(
            r"^\s*#\s*[A-Za-z_][A-Za-z0-9_]*"
        )

        self.c_function_pattern = re.compile(
            r"\b[A-Za-z_][A-Za-z0-9_]*(?=\s*\()"
        )

        self.c_char_pattern = re.compile(
            r"'(?:\\.|[^'\\])*'"
        )

        self.c_number_pattern = re.compile(
            r"\b(?:"
            r"0[xX][0-9A-Fa-f]+"
            r"|0[bB][01]+"
            r"|0[0-7]+"
            r"|(?:\d+(?:\.\d*)?|\.\d+)"
            r"(?:[eE][+-]?\d+)?"
            r"[fFlL]?"
            r")\b"
        )

        self.c_operator_pattern = re.compile(
            r"->|"
            r"\+\+|--|"
            r"==|!=|<=|>=|"
            r"&&|\|\||"
            r"<<|>>|"
            r"\+=|-=|\*=|/=|%=|"
            r"&=|\|=|\^=|"
            r"<<=|>>=|"
            r"[+\-*/%=<>!&|^~?:]"
        )

        self.string_pattern = re.compile(
            r'"(?:\\.|[^"\\])*"'
        )

        self.single_string_pattern = re.compile(
            r"'(?:\\.|[^'\\])*'"
        )

        self.number_pattern = re.compile(
            r"\b-?(?:0|[1-9]\d*)(?:\.\d+)?"
            r"(?:[eE][+-]?\d+)?\b"
        )

        self.boolean_pattern = re.compile(
            r"\b(?:true|false)\b"
        )

        self.null_pattern = re.compile(
            r"\bnull\b"
        )

        self.json_key_pattern = re.compile(
            r'"(?:\\.|[^"\\])*"(?=\s*:)'
        )

        self.cfg_key_pattern = re.compile(
            r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)"
            r"(\s*[:=])"
        )

        self.cfg_section_pattern = re.compile(
            r"^\s*\[[^\]]+\]"
        )

        self.cfg_comment_pattern = re.compile(
            r"^\s*(?://|#|;).*$"
        )

        self.xml_tag_pattern = re.compile(
            r"</?[A-Za-z_][\w:.-]*|"
            r"/>|"
            r">"
        )

        self.xml_attribute_pattern = re.compile(
            r"\b[A-Za-z_][\w:.-]*(?=\s*=)"
        )

        self.xml_double_string_pattern = re.compile(
            r'"(?:[^"\\]|\\.)*"'
        )

        self.xml_single_string_pattern = re.compile(
            r"'(?:[^'\\]|\\.)*'"
        )

        self.xml_entity_pattern = re.compile(
            r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);"
        )

        self.xml_declaration_pattern = re.compile(
            r"<\?xml\b.*?\?>"
        )

        self.xml_processing_pattern = re.compile(
            r"<\?.*?\?>"
        )

    def set_file_type(
        self,
        extension,
    ):
        self.file_type = (
            extension or ""
        ).lower()

        self.rehighlight()

    def highlightBlock(
        self,
        text,
    ):
        if self.file_type == ".json":
            self._highlight_json(
                text
            )

        elif self.file_type == ".cfg":
            self._highlight_cfg(
                text
            )

        elif self.file_type == ".xml":
            self._highlight_xml(
                text
            )

        elif self.file_type == ".txt":
            self._highlight_txt(
                text
            )

        elif self.file_type == ".c":
            self._highlight_c(
                text
            )

    # --------------------------------------------------------------
    # JSON
    # --------------------------------------------------------------

    def _highlight_json(
        self,
        text,
    ):
        comment_start = None

        in_string = False
        escaped = False

        for index, char in enumerate(
            text
        ):
            if escaped:
                escaped = False
                continue

            if (
                char == "\\"
                and in_string
            ):
                escaped = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if (
                not in_string
                and char == "/"
                and index + 1 < len(text)
                and text[index + 1] == "/"
            ):
                comment_start = index
                break

        if comment_start is not None:
            self.setFormat(
                comment_start,
                len(text) - comment_start,
                self.comment_format,
            )

            code_text = text[
                :comment_start
            ]

        else:
            code_text = text

        for match in self.string_pattern.finditer(
            code_text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.string_format,
            )

        for match in self.json_key_pattern.finditer(
            code_text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.key_format,
            )

        for match in self.number_pattern.finditer(
            code_text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.number_format,
            )

        for match in self.boolean_pattern.finditer(
            code_text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.boolean_format,
            )

        for match in self.null_pattern.finditer(
            code_text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.null_format,
            )

    # --------------------------------------------------------------
    # CFG
    # --------------------------------------------------------------

    def _highlight_cfg(
        self,
        text,
    ):
        comment_match = (
            self.cfg_comment_pattern.match(
                text
            )
        )

        if comment_match:
            self.setFormat(
                0,
                len(text),
                self.comment_format,
            )
            return

        section_match = (
            self.cfg_section_pattern.match(
                text
            )
        )

        if section_match:
            self.setFormat(
                section_match.start(),
                section_match.end()
                - section_match.start(),
                self.section_format,
            )

        key_match = (
            self.cfg_key_pattern.match(
                text
            )
        )

        if key_match:
            self.setFormat(
                key_match.start(1),
                key_match.end(1)
                - key_match.start(1),
                self.key_format,
            )

            self.setFormat(
                key_match.start(2),
                key_match.end(2)
                - key_match.start(2),
                self.operator_format,
            )

        for match in self.string_pattern.finditer(
            text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.string_format,
            )

        for match in self.number_pattern.finditer(
            text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.number_format,
            )

        for match in self.boolean_pattern.finditer(
            text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.boolean_format,
            )

    # --------------------------------------------------------------
    # TXT
    # --------------------------------------------------------------

    def _highlight_txt(
        self,
        text,
    ):
        if self.cfg_comment_pattern.match(
            text
        ):
            self.setFormat(
                0,
                len(text),
                self.comment_format,
            )
            return

        key_match = (
            self.cfg_key_pattern.match(
                text
            )
        )

        if key_match:
            self.setFormat(
                key_match.start(1),
                key_match.end(1)
                - key_match.start(1),
                self.key_format,
            )

        for match in self.string_pattern.finditer(
            text
        ):
            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.string_format,
            )

    # --------------------------------------------------------------
    # C
    # --------------------------------------------------------------

    def _highlight_c(
        self,
        text,
    ):
        """
        Highlight C source files.

        Handles:
            // single-line comments
            /* multiline comments */
            strings
            character literals
            preprocessor directives
            keywords
            built-in/common C types
            function calls
            numbers
            operators
        """

        comment_ranges = []

        # ----------------------------------------------------------
        # Multiline C comments.
        #
        # State 1 means the previous block ended while inside
        # a /* ... */ comment.
        # ----------------------------------------------------------

        code_ranges = []

        if self.previousBlockState() == 1:
            comment_end = text.find(
                "*/"
            )

            if comment_end == -1:
                self.setFormat(
                    0,
                    len(text),
                    self.comment_format,
                )

                self.setCurrentBlockState(
                    1
                )

                return

            comment_end += 2

            self.setFormat(
                0,
                comment_end,
                self.comment_format,
            )

            comment_ranges.append(
                (
                    0,
                    comment_end,
                )
            )

            code_start = comment_end

        else:
            code_start = 0

        self.setCurrentBlockState(
            0
        )

        # ----------------------------------------------------------
        # Find comments in the current line.
        # ----------------------------------------------------------

        search_position = code_start

        while search_position < len(text):
            line_comment_start = text.find(
                "//",
                search_position,
            )

            block_comment_start = text.find(
                "/*",
                search_position,
            )

            starts = [
                position
                for position in (
                    line_comment_start,
                    block_comment_start,
                )
                if position != -1
            ]

            if not starts:
                break

            comment_start = min(
                starts
            )

            # ------------------------------------------------------
            # Ignore comment markers inside strings/character
            # literals.
            # ------------------------------------------------------

            in_string = False
            in_char = False
            escaped = False
            real_comment_start = None

            index = search_position

            while index < len(text):
                char = text[index]

                if escaped:
                    escaped = False
                    index += 1
                    continue

                if (
                    char == "\\"
                    and (
                        in_string
                        or in_char
                    )
                ):
                    escaped = True
                    index += 1
                    continue

                if (
                    not in_char
                    and char == '"'
                ):
                    in_string = not in_string
                    index += 1
                    continue

                if (
                    not in_string
                    and char == "'"
                ):
                    in_char = not in_char
                    index += 1
                    continue

                if (
                    not in_string
                    and not in_char
                    and (
                        (
                            char == "/"
                            and index + 1 < len(text)
                            and text[index + 1] == "/"
                        )
                        or (
                            char == "/"
                            and index + 1 < len(text)
                            and text[index + 1] == "*"
                        )
                    )
                ):
                    real_comment_start = index
                    break

                index += 1

            if real_comment_start is None:
                break

            if (
                text.startswith(
                    "//",
                    real_comment_start,
                )
            ):
                self.setFormat(
                    real_comment_start,
                    len(text) - real_comment_start,
                    self.comment_format,
                )

                comment_ranges.append(
                    (
                        real_comment_start,
                        len(text),
                    )
                )

                break

            # ------------------------------------------------------
            # Block comment.
            # ------------------------------------------------------

            comment_end = text.find(
                "*/",
                real_comment_start + 2,
            )

            if comment_end == -1:
                self.setFormat(
                    real_comment_start,
                    len(text) - real_comment_start,
                    self.comment_format,
                )

                comment_ranges.append(
                    (
                        real_comment_start,
                        len(text),
                    )
                )

                self.setCurrentBlockState(
                    1
                )

                break

            comment_end += 2

            self.setFormat(
                real_comment_start,
                comment_end - real_comment_start,
                self.comment_format,
            )

            comment_ranges.append(
                (
                    real_comment_start,
                    comment_end,
                )
            )

            search_position = comment_end

        # ----------------------------------------------------------
        # Ranges that are safe for normal code highlighting.
        # ----------------------------------------------------------

        def overlaps_comment(
            start,
            end,
        ):
            return self._overlaps_any(
                start,
                end,
                comment_ranges,
            )

        # ----------------------------------------------------------
        # Preprocessor directive.
        # ----------------------------------------------------------

        preprocessor_match = (
            self.c_preprocessor_pattern.match(
                text
            )
        )

        if preprocessor_match:
            start = preprocessor_match.start()
            end = preprocessor_match.end()

            if not overlaps_comment(
                start,
                end,
            ):
                self.setFormat(
                    start,
                    end - start,
                    self.c_preprocessor_format,
                )

        # ----------------------------------------------------------
        # Strings.
        # ----------------------------------------------------------

        string_ranges = []

        for match in self.string_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.string_format,
            )

            string_ranges.append(
                (
                    match.start(),
                    match.end(),
                )
            )

        # ----------------------------------------------------------
        # Character literals.
        # ----------------------------------------------------------

        char_ranges = []

        for match in self.c_char_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.c_char_format,
            )

            char_ranges.append(
                (
                    match.start(),
                    match.end(),
                )
            )

        # ----------------------------------------------------------
        # Combine quoted ranges so keywords/numbers/operators
        # aren't highlighted inside strings or character literals.
        # ----------------------------------------------------------

        quoted_ranges = (
            string_ranges
            + char_ranges
        )

        # ----------------------------------------------------------
        # Keywords.
        # ----------------------------------------------------------

        for match in self.c_keyword_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            if self._overlaps_any(
                match.start(),
                match.end(),
                quoted_ranges,
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.c_keyword_format,
            )

        # ----------------------------------------------------------
        # C types.
        # ----------------------------------------------------------

        for match in self.c_type_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            if self._overlaps_any(
                match.start(),
                match.end(),
                quoted_ranges,
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.c_type_format,
            )

        # ----------------------------------------------------------
        # Function calls.
        #
        # Apply after keywords/types so common calls such as
        # printf(), malloc(), etc. receive function highlighting.
        # ----------------------------------------------------------

        for match in self.c_function_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            if self._overlaps_any(
                match.start(),
                match.end(),
                quoted_ranges,
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.c_function_format,
            )

        # ----------------------------------------------------------
        # Numbers.
        # ----------------------------------------------------------

        for match in self.c_number_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            if self._overlaps_any(
                match.start(),
                match.end(),
                quoted_ranges,
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.c_number_format,
            )

        # ----------------------------------------------------------
        # Operators.
        # ----------------------------------------------------------

        for match in self.c_operator_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            if self._overlaps_any(
                match.start(),
                match.end(),
                quoted_ranges,
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.c_operator_format,
            )

    # --------------------------------------------------------------
    # XML
    # --------------------------------------------------------------

    def _highlight_xml(
        self,
        text,
    ):
        """
        Highlight XML while correctly handling multiline comments.
        """

        comment_ranges = []

        if self.previousBlockState() == 1:
            comment_end = text.find(
                "-->"
            )

            if comment_end == -1:
                self.setFormat(
                    0,
                    len(text),
                    self.comment_format,
                )

                self.setCurrentBlockState(
                    1
                )

                return

            comment_end += 3

            self.setFormat(
                0,
                comment_end,
                self.comment_format,
            )

            comment_ranges.append(
                (
                    0,
                    comment_end,
                )
            )

            code_start = comment_end

        else:
            code_start = 0

        self.setCurrentBlockState(
            0
        )

        search_position = code_start

        while search_position < len(text):
            comment_start = text.find(
                "<!--",
                search_position,
            )

            if comment_start == -1:
                break

            comment_end = text.find(
                "-->",
                comment_start + 4,
            )

            if comment_end == -1:
                self.setFormat(
                    comment_start,
                    len(text) - comment_start,
                    self.comment_format,
                )

                comment_ranges.append(
                    (
                        comment_start,
                        len(text),
                    )
                )

                self.setCurrentBlockState(
                    1
                )

                break

            comment_end += 3

            self.setFormat(
                comment_start,
                comment_end - comment_start,
                self.comment_format,
            )

            comment_ranges.append(
                (
                    comment_start,
                    comment_end,
                )
            )

            search_position = comment_end

        def overlaps_comment(
            start,
            end,
        ):
            return self._overlaps_any(
                start,
                end,
                comment_ranges,
            )

        for match in self.xml_declaration_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.xml_declaration_format,
            )

        for match in self.xml_processing_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.xml_declaration_format,
            )

        for match in self.xml_tag_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.xml_tag_format,
            )

        for match in self.xml_attribute_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.xml_attribute_format,
            )

        for match in self.xml_double_string_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.xml_string_format,
            )

        for match in self.xml_single_string_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.xml_string_format,
            )

        for match in self.xml_entity_pattern.finditer(
            text
        ):
            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.xml_entity_format,
            )

        quoted_ranges = []

        for match in self.xml_double_string_pattern.finditer(
            text
        ):
            quoted_ranges.append(
                (
                    match.start(),
                    match.end(),
                )
            )

        for match in self.xml_single_string_pattern.finditer(
            text
        ):
            quoted_ranges.append(
                (
                    match.start(),
                    match.end(),
                )
            )

        for match in self.number_pattern.finditer(
            text
        ):
            if self._overlaps_any(
                match.start(),
                match.end(),
                quoted_ranges,
            ):
                continue

            if overlaps_comment(
                match.start(),
                match.end(),
            ):
                continue

            self.setFormat(
                match.start(),
                match.end() - match.start(),
                self.number_format,
            )

    # --------------------------------------------------------------
    # XML helpers
    # --------------------------------------------------------------

    @staticmethod
    def _overlaps_any(
        start,
        end,
        ranges,
    ):
        for range_start, range_end in ranges:
            if (
                start < range_end
                and end > range_start
            ):
                return True

        return False

    def _overlaps_comment(
        self,
        start,
        end,
        comment_ranges,
    ):
        return self._overlaps_any(
            start,
            end,
            comment_ranges,
        )


# ================================================================
# Line Number Area
# ================================================================


class LineNumberArea(QWidget):
    def __init__(
        self,
        editor,
    ):
        super().__init__(
            editor
        )

        self.editor = editor

    def sizeHint(
        self,
    ):
        return self.editor.line_number_area_width()

    def paintEvent(
        self,
        event,
    ):
        self.editor.paint_line_numbers(
            event
        )


# ================================================================
# Code Editor
# ================================================================


class CodeEditor(QPlainTextEdit):
    save_requested = Signal()

    def __init__(self):
        super().__init__()

        self.line_number_area = (
            LineNumberArea(
                self
            )
        )

        self.validation_issues = []

        # ----------------------------------------------------------
        # Search state
        # ----------------------------------------------------------

        self.search_text = ""
        self.search_match_count = 0
        self.search_match_index = 0

        # ----------------------------------------------------------
        # Font
        # ----------------------------------------------------------

        font = QFontDatabase.systemFont(
            QFontDatabase.FixedFont
        )

        font.setPointSize(
            max(
                10,
                font.pointSize(),
            )
        )

        self.setFont(
            font
        )

        # ----------------------------------------------------------
        # Editor settings
        # ----------------------------------------------------------

        self.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap
        )

        self.setTabStopDistance(
            4
            * self.fontMetrics().horizontalAdvance(
                " "
            )
        )

        self.setUndoRedoEnabled(
            True
        )

        self.setPlaceholderText(
            "Select a marked config file..."
        )

        # ----------------------------------------------------------
        # Signals
        # ----------------------------------------------------------

        self.blockCountChanged.connect(
            self._update_line_number_area_width
        )

        self.updateRequest.connect(
            self._update_line_number_area
        )

        self.cursorPositionChanged.connect(
            self._highlight_current_line
        )

        self._update_line_number_area_width(
            0
        )

        self._highlight_current_line()

    # ==============================================================
    # Search
    # ==============================================================

    def set_search_text(
        self,
        text,
    ):
        """
        Set the current search text.

        Search is case-insensitive.
        """

        self.search_text = (
            text or ""
        )

        self.search_match_count = 0
        self.search_match_index = 0

        if not self.search_text:
            self._highlight_current_line()
            return 0, 0

        self._refresh_search_matches(
            forward=True
        )

        return (
            self.search_match_index,
            self.search_match_count,
        )

    def search_next(
        self,
    ):
        if not self.search_text:
            return 0, 0

        self._refresh_search_matches(
            forward=True
        )

        return (
            self.search_match_index,
            self.search_match_count,
        )

    def search_previous(
        self,
    ):
        if not self.search_text:
            return 0, 0

        self._refresh_search_matches(
            forward=False
        )

        return (
            self.search_match_index,
            self.search_match_count,
        )

    def clear_search(
        self,
    ):
        self.search_text = ""
        self.search_match_count = 0
        self.search_match_index = 0

        self._highlight_current_line()

    def _refresh_search_matches(
        self,
        forward=True,
    ):
        text = self.search_text

        if not text:
            self.search_match_count = 0
            self.search_match_index = 0
            self._highlight_current_line()
            return

        document = self.document()

        matches = []

        search_cursor = QTextCursor(
            document
        )

        search_cursor.movePosition(
            QTextCursor.MoveOperation.Start
        )

        flags = QTextDocument.FindFlag(0)

        while True:
            match_cursor = document.find(
                text,
                search_cursor,
                flags,
            )

            if match_cursor.isNull():
                break

            start = match_cursor.selectionStart()
            end = match_cursor.selectionEnd()

            matches.append(
                (
                    start,
                    end,
                )
            )

            next_position = end

            if next_position <= start:
                next_position = start + 1

            if next_position >= document.characterCount():
                break

            search_cursor = QTextCursor(
                document
            )

            search_cursor.setPosition(
                next_position
            )

        self.search_match_count = len(
            matches
        )

        if not matches:
            self.search_match_index = 0

            current_cursor = self.textCursor()
            current_cursor.clearSelection()
            self.setTextCursor(
                current_cursor
            )

            self._highlight_current_line()
            return

        current_position = (
            self.textCursor().position()
        )

        if forward:
            selected_index = None

            for index, (
                start,
                end,
            ) in enumerate(
                matches
            ):
                if start > current_position:
                    selected_index = index
                    break

            if selected_index is None:
                selected_index = 0

        else:
            selected_index = None

            for index in range(
                len(matches) - 1,
                -1,
                -1,
            ):
                start, end = matches[index]

                if end < current_position:
                    selected_index = index
                    break

            if selected_index is None:
                selected_index = len(
                    matches
                ) - 1

        self.search_match_index = (
            selected_index + 1
        )

        start, end = matches[
            selected_index
        ]

        cursor = QTextCursor(
            document
        )

        cursor.setPosition(
            start
        )

        cursor.setPosition(
            end,
            QTextCursor.MoveMode.KeepAnchor,
        )

        self.setTextCursor(
            cursor
        )

        self.ensureCursorVisible()

        self._highlight_current_line()

    # ==============================================================
    # Validation markers
    # ==============================================================

    def set_validation_issues(
        self,
        issues,
    ):
        self.validation_issues = list(
            issues or []
        )

        self._update_line_number_area_width(
            0
        )

        self.line_number_area.update()

        self._highlight_current_line()

    def clear_validation_issues(
        self,
    ):
        self.set_validation_issues(
            []
        )

    def issues_for_line(
        self,
        line_number,
    ):
        return [
            issue
            for issue in self.validation_issues
            if issue.line == line_number
        ]

    # ==============================================================
    # Line numbers
    # ==============================================================

    def line_number_area_width(
        self,
    ):
        digits = 1

        maximum = max(
            1,
            self.blockCount(),
        )

        while maximum >= 10:
            maximum //= 10
            digits += 1

        marker_width = 18

        return (
            12
            + marker_width
            + self.fontMetrics().horizontalAdvance(
                "9"
            )
            * digits
        )

    def _update_line_number_area_width(
        self,
        _block_count,
    ):
        self.setViewportMargins(
            self.line_number_area_width(),
            0,
            0,
            0,
        )

    def _update_line_number_area(
        self,
        rect,
        dy,
    ):
        if dy:
            self.line_number_area.scroll(
                0,
                dy,
            )

        else:
            self.line_number_area.update(
                0,
                rect.y(),
                self.line_number_area.width(),
                rect.height(),
            )

        if rect.contains(
            self.viewport().rect()
        ):
            self._update_line_number_area_width(
                0
            )

    def resizeEvent(
        self,
        event,
    ):
        super().resizeEvent(
            event
        )

        rect = self.contentsRect()

        self.line_number_area.setGeometry(
            rect.left(),
            rect.top(),
            self.line_number_area_width(),
            rect.height(),
        )

    def paint_line_numbers(
        self,
        event,
    ):
        painter = QPainter(
            self.line_number_area
        )

        try:
            painter.fillRect(
                event.rect(),
                QColor("#1E1E1E"),
            )

            normal_font = QFont(
                self.font()
            )

            normal_font.setBold(
                False
            )

            bold_font = QFont(
                self.font()
            )

            bold_font.setBold(
                True
            )

            block = self.firstVisibleBlock()

            block_number = (
                block.blockNumber()
            )

            top = int(
                self.blockBoundingGeometry(
                    block
                ).translated(
                    self.contentOffset()
                ).top()
            )

            bottom = (
                top
                + int(
                    self.blockBoundingRect(
                        block
                    ).height()
                )
            )

            while (
                block.isValid()
                and top <= event.rect().bottom()
            ):
                if (
                    block.isVisible()
                    and bottom >= event.rect().top()
                ):
                    number = str(
                        block_number + 1
                    )

                    issues = self.issues_for_line(
                        block_number + 1
                    )

                    if issues:
                        severity = "hint"

                        for issue in issues:
                            if issue.severity == "error":
                                severity = "error"
                                break

                            if (
                                issue.severity == "warning"
                                and severity == "hint"
                            ):
                                severity = "warning"

                        if severity == "error":
                            marker_color = QColor(
                                "#F44747"
                            )

                        elif severity == "warning":
                            marker_color = QColor(
                                "#CCA700"
                            )

                        else:
                            marker_color = QColor(
                                "#569CD6"
                            )

                        painter.setPen(
                            marker_color
                        )

                        painter.setFont(
                            bold_font
                        )

                        painter.drawText(
                            3,
                            top,
                            14,
                            self.fontMetrics().height(),
                            Qt.AlignmentFlag.AlignCenter,
                            "●",
                        )

                    painter.setPen(
                        QColor("#858585")
                    )

                    painter.setFont(
                        normal_font
                    )

                    painter.drawText(
                        18,
                        top,
                        self.line_number_area.width() - 24,
                        self.fontMetrics().height(),
                        Qt.AlignmentFlag.AlignRight,
                        number,
                    )

                block = block.next()

                top = bottom

                if block.isValid():
                    bottom = (
                        top
                        + int(
                            self.blockBoundingRect(
                                block
                            ).height()
                        )
                    )

                block_number += 1

        finally:
            painter.end()

    # ==============================================================
    # Current line / search highlighting
    # ==============================================================

    def _highlight_current_line(
        self,
    ):
        selections = []

        current = QTextEdit.ExtraSelection()

        current.format.setBackground(
            QColor("#252526")
        )

        current.format.setProperty(
            QTextCharFormat.FullWidthSelection,
            True,
        )

        current.cursor = self.textCursor()
        current.cursor.clearSelection()

        selections.append(
            current
        )

        for issue in self.validation_issues:
            if issue.line < 1:
                continue

            block = self.document().findBlockByNumber(
                issue.line - 1
            )

            if not block.isValid():
                continue

            selection = QTextEdit.ExtraSelection()

            if issue.severity == "error":
                background = QColor(
                    "#4A2020"
                )

            elif issue.severity == "warning":
                background = QColor(
                    "#453D20"
                )

            else:
                background = QColor(
                    "#202F45"
                )

            selection.format.setBackground(
                background
            )

            selection.format.setProperty(
                QTextCharFormat.FullWidthSelection,
                True,
            )

            cursor = QTextCursor(
                self.document()
            )

            cursor.setPosition(
                block.position()
            )

            cursor.clearSelection()

            selection.cursor = cursor

            selections.append(
                selection
            )

        if (
            self.search_text
            and self.textCursor().hasSelection()
        ):
            search_selection = QTextEdit.ExtraSelection()

            search_selection.format.setBackground(
                QColor("#264F78")
            )

            search_selection.format.setForeground(
                QColor("#FFFFFF")
            )

            search_selection.cursor = (
                self.textCursor()
            )

            selections.append(
                search_selection
            )

        self.setExtraSelections(
            selections
        )

    # ==============================================================
    # Keyboard
    # ==============================================================

    def keyPressEvent(
        self,
        event,
    ):
        if event.matches(
            QKeySequence.StandardKey.Save
        ):
            self.save_requested.emit()
            return

        if (
            event.key() == Qt.Key.Key_Tab
            and not event.modifiers()
        ):
            self.insertPlainText(
                "    "
            )
            return

        super().keyPressEvent(
            event
        )


# ================================================================
# Marked Files List
# ================================================================


class MarkedFilesListWidget(QListWidget):
    """
    Custom mouse-based drag/reorder list for marked config files.

    This mirrors the behaviour of the Mods panel without using
    Qt's native drag/drop system.
    """

    reorder_requested = Signal(int, int)

    def __init__(
        self,
        parent=None,
    ):
        super().__init__(
            parent
        )

        self._pressed_row = -1
        self._press_pos = None
        self._dragging = False
        self._drop_row = -1
        self._drop_after = False

        # ----------------------------------------------------------
        # Disable Qt native drag/drop.
        # ----------------------------------------------------------

        self.setDragEnabled(
            False
        )

        self.setAcceptDrops(
            False
        )

        self.setDropIndicatorShown(
            False
        )

        self.setMouseTracking(
            True
        )

    # ==============================================================
    # Mouse handling
    # ==============================================================

    def mousePressEvent(
        self,
        event,
    ):
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()

            item = self.itemAt(
                position
            )

            if item is not None:
                self._pressed_row = self.row(
                    item
                )

                self._press_pos = position

                self._dragging = False
                self._drop_row = -1
                self._drop_after = False

        super().mousePressEvent(
            event
        )

    def mouseMoveEvent(
        self,
        event,
    ):
        if (
            self._pressed_row >= 0
            and self._press_pos is not None
            and event.buttons()
            & Qt.MouseButton.LeftButton
        ):
            current_pos = event.position().toPoint()

            distance = (
                current_pos
                - self._press_pos
            ).manhattanLength()

            if (
                not self._dragging
                and distance
                >= QApplication.startDragDistance()
            ):
                self._dragging = True

                self.setCursor(
                    Qt.CursorShape.ClosedHandCursor
                )

            if self._dragging:
                self._update_drop_indicator(
                    current_pos.y()
                )

                self.viewport().update()

                return

        super().mouseMoveEvent(
            event
        )

    def mouseReleaseEvent(
        self,
        event,
    ):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._dragging
        ):
            source_row = self._pressed_row

            destination_row = (
                self._calculate_destination_row()
            )

            self._reset_drag_state()

            if (
                source_row >= 0
                and destination_row >= 0
                and source_row != destination_row
            ):
                self.reorder_requested.emit(
                    source_row,
                    destination_row,
                )

            self.viewport().update()

            return

        self._reset_drag_state()

        super().mouseReleaseEvent(
            event
        )

    # ==============================================================
    # Drop indicator
    # ==============================================================

    def _update_drop_indicator(
        self,
        y,
    ):
        count = self.count()

        if count <= 0:
            self._drop_row = -1
            self._drop_after = False
            return

        item = self.itemAt(
            2,
            y,
        )

        if item is None:
            # ------------------------------------------------------
            # Above the first item.
            # ------------------------------------------------------

            if y < 0:
                self._drop_row = 0
                self._drop_after = False
                return

            # ------------------------------------------------------
            # Below the last item.
            # ------------------------------------------------------

            last_item = self.item(
                count - 1
            )

            last_rect = self.visualItemRect(
                last_item
            )

            if y > last_rect.bottom():
                self._drop_row = count - 1
                self._drop_after = True
                return

            self._drop_row = -1
            self._drop_after = False
            return

        row = self.row(
            item
        )

        rect = self.visualItemRect(
            item
        )

        midpoint = (
            rect.top()
            + rect.height() / 2
        )

        self._drop_row = row

        self._drop_after = (
            y >= midpoint
        )

    def _calculate_destination_row(
        self,
    ):
        if self._drop_row < 0:
            return -1

        destination = self._drop_row

        if self._drop_after:
            destination += 1

        # ----------------------------------------------------------
        # Account for the source item disappearing before insertion.
        # ----------------------------------------------------------

        if self._pressed_row < destination:
            destination -= 1

        if destination < 0:
            destination = 0

        if destination >= self.count():
            destination = self.count() - 1

        return destination

    def _reset_drag_state(
        self,
    ):
        self._pressed_row = -1
        self._press_pos = None
        self._dragging = False
        self._drop_row = -1
        self._drop_after = False

        self.setCursor(
            Qt.CursorShape.ArrowCursor
        )

    # ==============================================================
    # Painting
    # ==============================================================

    def paintEvent(
        self,
        event,
    ):
        super().paintEvent(
            event
        )

        if (
            not self._dragging
            or self._drop_row < 0
            or self.count() <= 0
        ):
            return

        item = self.item(
            self._drop_row
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
                QColor("#4A9EFF")
            )

            pen.setWidth(
                2
            )

            painter.setPen(
                pen
            )

            viewport_width = (
                self.viewport().width()
            )

            painter.drawLine(
                4,
                y,
                viewport_width - 4,
                y,
            )

            painter.setBrush(
                QColor("#4A9EFF")
            )

            painter.setPen(
                Qt.PenStyle.NoPen
            )

            painter.drawEllipse(
                1,
                y - 3,
                6,
                6,
            )

            painter.drawEllipse(
                viewport_width - 7,
                y - 3,
                6,
                6,
            )

        finally:
            painter.end()


# ================================================================
# Config Editor Panel
# ================================================================


class ConfigEditorPanel(QWidget):
    """
    Editor for files explicitly marked from the Server Files panel.

    The Config Editor never scans the server.

    Files are controlled by:

        config.marked_config_files
    """

    def __init__(
        self,
        ssh,
        config,
    ):
        super().__init__()

        self.ssh = ssh
        self.config = config
        self.jobs = WorkerRegistry()

        self.current_path = None

        self.validator = ConfigValidator()

        self.validation_issues = []

        # ----------------------------------------------------------
        # Debounced validation
        # ----------------------------------------------------------

        self.validation_timer = QTimer(
            self
        )

        self.validation_timer.setSingleShot(
            True
        )

        self.validation_timer.setInterval(
            350
        )

        self.validation_timer.timeout.connect(
            self.validate_current
        )

        # ----------------------------------------------------------
        # Debounced search
        # ----------------------------------------------------------

        self.search_timer = QTimer(
            self
        )

        self.search_timer.setSingleShot(
            True
        )

        self.search_timer.setInterval(
            150
        )

        self.search_timer.timeout.connect(
            self._perform_search
        )

        self._building_editor = False

        self._build_ui()

        self.editor.textChanged.connect(
            self._schedule_validation
        )

        self.set_connected(
            False
        )

    # ==============================================================
    # UI
    # ==============================================================

    def _build_ui(
        self,
    ):
        layout = QVBoxLayout(
            self
        )

        layout.setContentsMargins(
            8,
            8,
            8,
            8,
        )

        layout.setSpacing(
            6
        )

        # ----------------------------------------------------------
        # Top row
        # ----------------------------------------------------------

        top_row = QHBoxLayout()

        self.title_label = QLabel(
            "Config Editor"
        )

        self.title_label.setStyleSheet(
            """
            QLabel {
                font-size: 15px;
                font-weight: bold;
            }
            """
        )

        top_row.addWidget(
            self.title_label
        )

        top_row.addStretch()

        self.refresh_btn = QPushButton(
            "Refresh"
        )

        self.refresh_btn.clicked.connect(
            self.refresh_list
        )

        top_row.addWidget(
            self.refresh_btn
        )

        self.remove_btn = QPushButton(
            "Remove Selected"
        )

        self.remove_btn.clicked.connect(
            self.remove_selected
        )

        top_row.addWidget(
            self.remove_btn
        )

        layout.addLayout(
            top_row
        )

        # ----------------------------------------------------------
        # Splitter
        # ----------------------------------------------------------

        splitter = QSplitter(
            Qt.Orientation.Horizontal
        )

        # ==========================================================
        # LEFT PANEL
        # ==========================================================

        left = QWidget()

        left_layout = QVBoxLayout(
            left
        )

        left_layout.setContentsMargins(
            0,
            0,
            4,
            0,
        )

        left_title = QLabel(
            "Marked Files"
        )

        left_title.setStyleSheet(
            """
            QLabel {
                font-weight: bold;
                padding: 4px;
            }
            """
        )

        left_layout.addWidget(
            left_title
        )

        self.file_list = MarkedFilesListWidget()

        self.file_list.setAlternatingRowColors(
            True
        )

        self.file_list.setSpacing(
            0
        )

        self.file_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.file_list.reorder_requested.connect(
            self._marked_files_reordered
        )

        self.file_list.currentItemChanged.connect(
            self._file_item_changed
        )

        left_layout.addWidget(
            self.file_list
        )

        splitter.addWidget(
            left
        )

        # ==========================================================
        # RIGHT PANEL
        # ==========================================================

        right = QWidget()

        right_layout = QVBoxLayout(
            right
        )

        right_layout.setContentsMargins(
            4,
            0,
            0,
            0,
        )

        # ----------------------------------------------------------
        # File header
        # ----------------------------------------------------------

        header = QHBoxLayout()

        self.file_name_label = QLabel(
            "No file selected"
        )

        self.file_name_label.setStyleSheet(
            """
            QLabel {
                font-size: 14px;
                font-weight: bold;
            }
            """
        )

        header.addWidget(
            self.file_name_label
        )

        header.addStretch()

        self.file_type_label = QLabel(
            ""
        )

        self.file_type_label.setStyleSheet(
            """
            QLabel {
                color: #888888;
                padding-right: 4px;
            }
            """
        )

        header.addWidget(
            self.file_type_label
        )

        right_layout.addLayout(
            header
        )

        # ----------------------------------------------------------
        # Full path
        # ----------------------------------------------------------

        self.path_label = QLabel(
            "No file selected"
        )

        self.path_label.setWordWrap(
            False
        )

        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.path_label.setStyleSheet(
            """
            QLabel {
                color: #808080;
                padding-bottom: 4px;
            }
            """
        )

        right_layout.addWidget(
            self.path_label
        )

        # ----------------------------------------------------------
        # Search bar
        # ----------------------------------------------------------

        search_row = QHBoxLayout()

        self.search_input = QLineEdit()

        self.search_input.setPlaceholderText(
            "Search in config..."
        )

        self.search_input.setClearButtonEnabled(
            True
        )

        self.search_input.setMinimumWidth(
            220
        )

        self.search_input.textChanged.connect(
            self._schedule_search
        )

        self.search_input.returnPressed.connect(
            self._search_next
        )

        search_row.addWidget(
            self.search_input
        )

        self.search_previous_btn = QPushButton(
            "Previous"
        )

        self.search_previous_btn.setToolTip(
            "Previous match (Shift+Enter)"
        )

        self.search_previous_btn.clicked.connect(
            self._search_previous
        )

        search_row.addWidget(
            self.search_previous_btn
        )

        self.search_next_btn = QPushButton(
            "Next"
        )

        self.search_next_btn.setToolTip(
            "Next match (Enter)"
        )

        self.search_next_btn.clicked.connect(
            self._search_next
        )

        search_row.addWidget(
            self.search_next_btn
        )

        self.search_count_label = QLabel(
            ""
        )

        self.search_count_label.setMinimumWidth(
            65
        )

        self.search_count_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.search_count_label.setStyleSheet(
            """
            QLabel {
                color: #888888;
            }
            """
        )

        search_row.addWidget(
            self.search_count_label
        )

        right_layout.addLayout(
            search_row
        )

        # ----------------------------------------------------------
        # Editor
        # ----------------------------------------------------------

        self.editor = CodeEditor()

        self.editor.save_requested.connect(
            self.save_current
        )

        self.editor.document().modificationChanged.connect(
            self._document_modified
        )

        self.editor.setEnabled(
            False
        )

        right_layout.addWidget(
            self.editor
        )

        # ----------------------------------------------------------
        # Syntax highlighter
        # ----------------------------------------------------------

        self.highlighter = ConfigSyntaxHighlighter(
            self.editor.document()
        )

        # ----------------------------------------------------------
        # Problems
        # ----------------------------------------------------------

        problems_header = QHBoxLayout()

        self.problems_label = QLabel(
            "Problems"
        )

        self.problems_label.setStyleSheet(
            """
            QLabel {
                font-weight: bold;
                padding-top: 4px;
            }
            """
        )

        problems_header.addWidget(
            self.problems_label
        )

        problems_header.addStretch()

        # ----------------------------------------------------------
        # Auto validation toggle
        # ----------------------------------------------------------

        self.auto_validate_checkbox = QCheckBox(
            "Auto Validate"
        )

        self.auto_validate_checkbox.setChecked(
            False
        )

        self.auto_validate_checkbox.setToolTip(
            "Automatically validate the file after editing. "
            "Disable this for large files to avoid validation "
            "while typing. The Validate button still works "
            "when automatic validation is disabled."
        )

        self.auto_validate_checkbox.toggled.connect(
            self._auto_validate_toggled
        )

        problems_header.addWidget(
            self.auto_validate_checkbox
        )

        self.validate_btn = QPushButton(
            "Validate"
        )

        self.validate_btn.clicked.connect(
            self.validate_current
        )

        problems_header.addWidget(
            self.validate_btn
        )

        right_layout.addLayout(
            problems_header
        )

        self.problems_list = QListWidget()

        self.problems_list.setMaximumHeight(
            150
        )

        self.problems_list.setSpacing(
            1
        )

        self.problems_list.itemClicked.connect(
            self._problem_clicked
        )

        right_layout.addWidget(
            self.problems_list
        )

        # ----------------------------------------------------------
        # Save row
        # ----------------------------------------------------------

        save_row = QHBoxLayout()

        self.status_label = QLabel(
            "No file selected"
        )

        self.status_label.setStyleSheet(
            """
            QLabel {
                color: #808080;
            }
            """
        )

        save_row.addWidget(
            self.status_label
        )

        save_row.addStretch()

        self.save_btn = QPushButton(
            "Save to server (creates .bak)"
        )

        self.save_btn.clicked.connect(
            self.save_current
        )

        self.save_btn.setEnabled(
            False
        )

        save_row.addWidget(
            self.save_btn
        )

        self.restore_btn = QPushButton(
            "Restore .bak"
        )

        self.restore_btn.setToolTip(
            "Replace the current server file with its .bak backup."
        )

        self.restore_btn.clicked.connect(
            self.restore_backup
        )

        self.restore_btn.setEnabled(
            False
        )

        save_row.addWidget(
            self.restore_btn
        )

        right_layout.addLayout(
            save_row
        )

        splitter.addWidget(
            right
        )

        splitter.setStretchFactor(
            0,
            2
        )

        splitter.setStretchFactor(
            1,
            5
        )

        splitter.setSizes(
            [
                420,
                980,
            ]
        )

        layout.addWidget(
            splitter
        )

    # ==============================================================
    # Search
    # ==============================================================

    def _schedule_search(
        self,
        _text=None,
    ):
        if not hasattr(
            self,
            "search_input",
        ):
            return

        self.search_timer.start()

    def _perform_search(
        self,
    ):
        text = self.search_input.text()

        if not text:
            self.editor.clear_search()
            self.search_count_label.setText("")
            return

        index, count = self.editor.set_search_text(
            text
        )

        self._update_search_count(
            index,
            count,
        )

    def _search_next(
        self,
    ):
        if not self.search_input.text():
            self.search_input.setFocus()
            return

        index, count = self.editor.search_next()

        self._update_search_count(
            index,
            count,
        )

        self.editor.setFocus()

    def _search_previous(
        self,
    ):
        if not self.search_input.text():
            self.search_input.setFocus()
            return

        index, count = self.editor.search_previous()

        self._update_search_count(
            index,
            count,
        )

        self.editor.setFocus()

    def _update_search_count(
        self,
        index,
        count,
    ):
        if not count:
            self.search_count_label.setText(
                "No matches"
            )

        else:
            self.search_count_label.setText(
                f"{index} of {count}"
            )

    def _clear_search(
        self,
    ):
        self.search_input.clear()
        self.editor.clear_search()
        self.search_count_label.clear()

    # ==============================================================
    # Connection state
    # ==============================================================

    def set_connected(
        self,
        connected,
    ):
        connected = bool(
            connected
        )

        self.refresh_btn.setEnabled(
            connected
        )

        self.remove_btn.setEnabled(
            connected
        )

        self.file_list.setEnabled(
            connected
        )

        self.editor.setEnabled(
            connected
        )

        self.restore_btn.setEnabled(
            connected and bool(self.current_path)
        )

        self.validate_btn.setEnabled(
            connected
        )

        self.auto_validate_checkbox.setEnabled(
            connected
        )

        self.search_input.setEnabled(
            connected
        )

        self.search_previous_btn.setEnabled(
            connected
        )

        self.search_next_btn.setEnabled(
            connected
        )

        if connected:
            self.refresh_list()

        else:
            self.file_list.clear()
            self._clear_editor()

    # ==============================================================
    # File helpers
    # ==============================================================

    def _file_display_name(
        self,
        path,
    ):
        return PurePosixPath(
            str(path)
        ).name

    def _file_extension(
        self,
        path,
    ):
        return PurePosixPath(
            str(path)
        ).suffix.lower()

    def _file_icon(
        self,
        path,
    ):
        return QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_FileIcon
        )

    # ==============================================================
    # Marked file list
    # ==============================================================

    def refresh_list(
        self,
    ):
        marked_files = list(
            getattr(
                self.config,
                "marked_config_files",
                [],
            )
        )

        current_path = self.current_path

        self.file_list.blockSignals(
            True
        )

        self.file_list.clear()

        for path in marked_files:
            path = str(
                path
            )

            item = QListWidgetItem()

            item.setIcon(
                self._file_icon(
                    path
                )
            )

            item.setData(
                Qt.ItemDataRole.UserRole,
                path,
            )

            item.setText(
                f"{self._file_display_name(path)}"
                f"    {path}"
            )

            self.file_list.addItem(
                item
            )

        # ----------------------------------------------------------
        # Preserve the currently selected file whenever possible.
        # ----------------------------------------------------------

        selected_row = -1

        if current_path:
            for row in range(
                self.file_list.count()
            ):
                item = self.file_list.item(
                    row
                )

                if item.data(
                    Qt.ItemDataRole.UserRole
                ) == current_path:
                    selected_row = row
                    break

        if selected_row >= 0:
            self.file_list.setCurrentRow(
                selected_row
            )

        elif marked_files:
            self.file_list.setCurrentRow(
                0
            )

        self.file_list.blockSignals(
            False
        )

        if not marked_files:
            self._clear_editor()

    def _marked_files_reordered(
        self,
        source_row,
        destination_row,
    ):
        """
        Reorder marked_config_files and persist the new order.
        """

        marked_files = list(
            getattr(
                self.config,
                "marked_config_files",
                [],
            )
        )

        if not marked_files:
            return

        if (
            source_row < 0
            or source_row >= len(marked_files)
        ):
            return

        if (
            destination_row < 0
            or destination_row >= len(marked_files)
        ):
            return

        if source_row == destination_row:
            return

        current_path = self.current_path

        moved_path = marked_files.pop(
            source_row
        )

        marked_files.insert(
            destination_row,
            moved_path,
        )

        self.config.marked_config_files = (
            marked_files
        )

        self.config.save()

        # ----------------------------------------------------------
        # Rebuild the visual list while keeping the currently open
        # file selected.
        # ----------------------------------------------------------

        self.file_list.blockSignals(
            True
        )

        self.file_list.clear()

        for path in marked_files:
            path = str(
                path
            )

            item = QListWidgetItem()

            item.setIcon(
                self._file_icon(
                    path
                )
            )

            item.setData(
                Qt.ItemDataRole.UserRole,
                path,
            )

            item.setText(
                f"{self._file_display_name(path)}"
                f"    {path}"
            )

            self.file_list.addItem(
                item
            )

        selected_row = -1

        if current_path:
            for row in range(
                self.file_list.count()
            ):
                item = self.file_list.item(
                    row
                )

                if item.data(
                    Qt.ItemDataRole.UserRole
                ) == current_path:
                    selected_row = row
                    break

        if selected_row >= 0:
            self.file_list.setCurrentRow(
                selected_row
            )

        elif self.file_list.count() > 0:
            self.file_list.setCurrentRow(
                0
            )

        self.file_list.blockSignals(
            False
        )

        self.status_label.setText(
            "Marked file order saved"
        )

    def add_marked_file(
        self,
        path,
    ):
        if not path:
            return

        path = str(
            path
        )

        marked_files = list(
            getattr(
                self.config,
                "marked_config_files",
                [],
            )
        )

        if path not in marked_files:
            marked_files.append(
                path
            )

            self.config.marked_config_files = (
                marked_files
            )

            self.config.save()

        self.refresh_list()

        for row in range(
            self.file_list.count()
        ):
            item = self.file_list.item(
                row
            )

            if item.data(
                Qt.ItemDataRole.UserRole
            ) == path:
                self.file_list.setCurrentRow(
                    row
                )
                break

    def remove_marked_file(
        self,
        path,
    ):
        if not path:
            return

        path = str(
            path
        )

        marked_files = list(
            getattr(
                self.config,
                "marked_config_files",
                [],
            )
        )

        if path not in marked_files:
            return

        marked_files.remove(
            path
        )

        self.config.marked_config_files = (
            marked_files
        )

        self.config.save()

        if self.current_path == path:
            self._clear_editor()

        self.refresh_list()

    # ==============================================================
    # Selection
    # ==============================================================

    def _file_item_changed(
        self,
        current,
        previous,
    ):
        if current is None:
            return

        path = current.data(
            Qt.ItemDataRole.UserRole
        )

        if path:
            self.load_selected(
                path
            )

    # ==============================================================
    # Remove selected
    # ==============================================================

    def remove_selected(
        self,
    ):
        item = self.file_list.currentItem()

        if item is None:
            return

        path = item.data(
            Qt.ItemDataRole.UserRole
        )

        if not path:
            return

        answer = QMessageBox.question(
            self,
            "Remove Config File",
            (
                "Remove this file from the Config Editor?\n\n"
                f"{path}\n\n"
                "The remote file will NOT be deleted."
            ),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        self.remove_marked_file(
            path
        )

    # ==============================================================
    # Load
    # ==============================================================

    def load_selected(
        self,
        path,
    ):
        if not path:
            return

        if not self.ssh.is_connected():
            return

        path = str(
            path
        )

        self.current_path = path

        extension = self._file_extension(
            path
        )

        self.file_name_label.setText(
            self._file_display_name(
                path
            )
        )

        self.file_type_label.setText(
            extension.upper().lstrip(".")
            if extension
            else "TEXT"
        )

        self.path_label.setText(
            path
        )

        self.status_label.setText(
            "Loading..."
        )

        self._clear_search()

        self.editor.clear()

        self.editor.setEnabled(
            True
        )

        self.save_btn.setEnabled(
            False
        )

        self.restore_btn.setEnabled(
            True
        )

        self.clear_validation()

        self.highlighter.set_file_type(
            extension
        )

        def task():
            return self.ssh.read_file(
                path
            )

        self.jobs.start(
            task,
            on_ok=lambda content:
            self._on_loaded(
                path,
                content,
            ),
            on_fail=lambda error:
            self._show_error(
                "Could not load file",
                error,
            ),
        )

    def _on_loaded(
        self,
        path,
        content,
    ):
        if self.current_path != path:
            return

        self.editor.blockSignals(
            True
        )

        self.editor.setPlainText(
            content
        )

        self.editor.document().setModified(
            False
        )

        self.editor.blockSignals(
            False
        )

        extension = self._file_extension(
            path
        )

        self.highlighter.set_file_type(
            extension
        )

        self.file_name_label.setText(
            self._file_display_name(
                path
            )
        )

        self.file_type_label.setText(
            extension.upper().lstrip(".")
            if extension
            else "TEXT"
        )

        self.path_label.setText(
            path
        )

        self.save_btn.setEnabled(
            True
        )

        self.validate_current()

    # ==============================================================
    # Live validation
    # ==============================================================

    def _schedule_validation(
        self,
    ):
        if self._building_editor:
            return

        if not self.current_path:
            return

        if not self.auto_validate_checkbox.isChecked():
            return

        self.status_label.setText(
            "Checking..."
        )

        self.validation_timer.start()

    def _auto_validate_toggled(
        self,
        enabled,
    ):
        enabled = bool(
            enabled
        )

        if not enabled:
            self.validation_timer.stop()

            if self.current_path:
                self.status_label.setText(
                    "Auto validation disabled"
                )

            return

        if self.current_path:
            self.status_label.setText(
                "Checking..."
            )

            self.validation_timer.start()

    def validate_current(
        self,
    ):
        if not self.current_path:
            return True

        path = self.current_path

        content = self.editor.toPlainText()

        issues = self.validator.validate(
            path,
            content,
        )

        self.validation_issues = issues

        self.editor.set_validation_issues(
            issues
        )

        self._populate_problems(
            issues
        )

        errors = [
            issue
            for issue in issues
            if issue.severity == "error"
        ]

        warnings = [
            issue
            for issue in issues
            if issue.severity == "warning"
        ]

        hints = [
            issue
            for issue in issues
            if issue.severity == "hint"
        ]

        if not issues:
            extension = self._file_extension(
                path
            )

            if extension in {
                ".json",
                ".xml",
            }:
                self.status_label.setText(
                    "✓ Valid"
                )

            else:
                self.status_label.setText(
                    "No validation issues"
                )

        elif errors:
            self.status_label.setText(
                f"❌ {len(errors)} error"
                f"{'s' if len(errors) != 1 else ''}"
                f"  •  "
                f"{len(warnings)} warning"
                f"{'s' if len(warnings) != 1 else ''}"
            )

        else:
            self.status_label.setText(
                f"⚠ {len(warnings)} warning"
                f"{'s' if len(warnings) != 1 else ''}"
                f"  •  "
                f"{len(hints)} hint"
                f"{'s' if len(hints) != 1 else ''}"
            )

        self._update_save_state()

        return not errors

    # ==============================================================
    # Problems panel
    # ==============================================================

    def _populate_problems(
        self,
        issues,
    ):
        self.problems_list.blockSignals(
            True
        )

        self.problems_list.clear()

        if not issues:
            item = QListWidgetItem(
                "✓ No problems detected"
            )

            item.setForeground(
                QColor("#6A9955")
            )

            self.problems_list.addItem(
                item
            )

            self.problems_list.blockSignals(
                False
            )

            return

        for issue in issues:
            if issue.severity == "error":
                prefix = "❌"
                color = QColor(
                    "#F44747"
                )

            elif issue.severity == "warning":
                prefix = "⚠"
                color = QColor(
                    "#CCA700"
                )

            else:
                prefix = "💡"
                color = QColor(
                    "#569CD6"
                )

            text = (
                f"{prefix} "
                f"Line {issue.line}"
            )

            if issue.column:
                text += (
                    f", column {issue.column}"
                )

            text += (
                f" — {issue.message}"
            )

            if issue.hint:
                text += (
                    f"\n    {issue.hint}"
                )

            item = QListWidgetItem(
                text
            )

            item.setForeground(
                color
            )

            item.setData(
                Qt.ItemDataRole.UserRole,
                issue,
            )

            self.problems_list.addItem(
                item
            )

        self.problems_list.blockSignals(
            False
        )

    def _problem_clicked(
        self,
        item,
    ):
        issue = item.data(
            Qt.ItemDataRole.UserRole
        )

        if not isinstance(
            issue,
            ValidationIssue,
        ):
            return

        block = self.editor.document().findBlockByNumber(
            max(
                0,
                issue.line - 1,
            )
        )

        if not block.isValid():
            return

        cursor = self.editor.textCursor()

        position = (
            block.position()
            + max(
                0,
                issue.column - 1,
            )
        )

        max_position = (
            block.position()
            + max(
                0,
                block.length() - 1,
            )
        )

        cursor.setPosition(
            min(
                position,
                max_position,
            )
        )

        self.editor.setTextCursor(
            cursor
        )

        self.editor.centerCursor()

        self.editor.setFocus()

    def clear_validation(
        self,
    ):
        self.validation_issues = []

        self.editor.clear_validation_issues()

        self.problems_list.clear()

    # ==============================================================
    # Save state
    # ==============================================================

    def _update_save_state(
        self,
    ):
        if not self.current_path:
            self.save_btn.setEnabled(
                False
            )
            return

        errors = any(
            issue.severity == "error"
            for issue in self.validation_issues
        )

        self.save_btn.setEnabled(
            not errors
        )

    # ==============================================================
    # Modified state
    # ==============================================================

    def _document_modified(
        self,
        modified,
    ):
        if not self.current_path:
            return

        if modified:
            self.save_btn.setEnabled(
                False
            )

            if self.auto_validate_checkbox.isChecked():
                self.status_label.setText(
                    "Modified — checking..."
                )

                self.validation_timer.start()

            else:
                self.status_label.setText(
                    "Modified — auto validation disabled"
                )

        else:
            self._update_save_state()

    # ==============================================================
    # Restore backup
    # ==============================================================

    def restore_backup(
        self,
    ):
        if not self.current_path:
            return

        if not self.ssh.is_connected():
            QMessageBox.information(
                self,
                "Not connected",
                (
                    "Click Connect on the Server Status "
                    "tab first."
                ),
            )
            return

        path = str(
            self.current_path
        )

        backup_path = f"{path}.bak"

        answer = QMessageBox.question(
            self,
            "Restore Backup",
            (
                "Restore this file from its .bak backup?\n\n"
                f"Current file:\n{path}\n\n"
                f"Backup:\n{backup_path}\n\n"
                "The current file will be replaced.\n"
                "The existing .bak file will be kept unchanged."
            ),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        self.save_btn.setEnabled(
            False
        )

        self.restore_btn.setEnabled(
            False
        )

        self.status_label.setText(
            "Restoring backup..."
        )

        def task():
            quoted_backup = shlex.quote(
                backup_path
            )

            quoted_path = shlex.quote(
                path
            )

            code, out, err = self.ssh.exec(
                "test -f "
                + quoted_backup
                + " && cp -- "
                + quoted_backup
                + " "
                + quoted_path
            )

            if code != 0:
                message = (
                    err.strip()
                    or out.strip()
                    or "Backup file was not found."
                )

                raise RuntimeError(
                    message
                )

            return True

        self.jobs.start(
            task,
            on_ok=lambda _result:
            self._restore_success(
                path
            ),
            on_fail=lambda error:
            self._restore_failed(
                error
            ),
        )

    def _restore_success(
        self,
        path,
    ):
        if self.current_path != path:
            return

        self.status_label.setText(
            "Backup restored — reloading..."
        )

        QMessageBox.information(
            self,
            "Backup Restored",
            (
                f"Restored:\n{path}\n\n"
                "The .bak backup was left unchanged."
            ),
        )

        self.load_selected(
            path
        )

    def _restore_failed(
        self,
        error,
    ):
        self.restore_btn.setEnabled(
            bool(self.current_path)
            and self.ssh.is_connected()
        )

        self._update_save_state()

        self.status_label.setText(
            "Restore failed"
        )

        self._show_error(
            "Could not restore backup",
            error,
        )

    # ==============================================================
    # Save
    # ==============================================================

    def save_current(
        self,
    ):
        if not self.current_path:
            return

        if not self.ssh.is_connected():
            QMessageBox.information(
                self,
                "Not connected",
                (
                    "Click Connect on the Server Status "
                    "tab first."
                ),
            )
            return

        if not self.validate_current():
            QMessageBox.warning(
                self,
                "Cannot Save",
                (
                    "The configuration contains errors "
                    "that need to be fixed before it can "
                    "be saved.\n\n"
                    "Check the Problems panel below the editor."
                ),
            )

            return

        warnings = [
            issue
            for issue in self.validation_issues
            if issue.severity == "warning"
        ]

        if warnings:
            answer = QMessageBox.question(
                self,
                "DayZ Configuration Warnings",
                (
                    f"This configuration has "
                    f"{len(warnings)} DayZ warning"
                    f"{'s' if len(warnings) != 1 else ''}.\n\n"
                    "The syntax is valid, but some settings "
                    "may not work as intended.\n\n"
                    "Do you want to save anyway?"
                ),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )

            if answer != QMessageBox.StandardButton.Yes:
                return

        content = self.editor.toPlainText()

        path = self.current_path

        self.save_btn.setEnabled(
            False
        )

        self.status_label.setText(
            "Saving..."
        )

        def task():
            self.ssh.write_file(
                path,
                content,
                backup=True,
            )

            return True

        self.jobs.start(
            task,
            on_ok=lambda _result:
            self._save_success(
                path
            ),
            on_fail=lambda error:
            self._save_failed(
                error
            ),
        )

    def _save_success(
        self,
        path,
    ):
        if self.current_path != path:
            return

        self.editor.document().setModified(
            False
        )

        self._update_save_state()

        self.restore_btn.setEnabled(
            self.ssh.is_connected()
        )

        self.status_label.setText(
            "Saved — backup created"
        )

        QMessageBox.information(
            self,
            "Saved",
            (
                f"Saved:\n{path}\n\n"
                "A .bak backup was created."
            ),
        )

    def _save_failed(
        self,
        error,
    ):
        self.restore_btn.setEnabled(
            bool(self.current_path)
            and self.ssh.is_connected()
        )

        self._update_save_state()

        self.status_label.setText(
            "Save failed"
        )

        self._show_error(
            "Could not save file",
            error,
        )

    # ==============================================================
    # Clear editor
    # ==============================================================

    def _clear_editor(
        self,
    ):
        self.current_path = None

        self.validation_timer.stop()
        self.search_timer.stop()

        self._building_editor = True

        self.editor.blockSignals(
            True
        )

        self.editor.clear()

        self.editor.document().setModified(
            False
        )

        self.editor.blockSignals(
            False
        )

        self._building_editor = False

        self.editor.set_validation_issues(
            []
        )

        self.problems_list.clear()

        if hasattr(
            self,
            "search_input",
        ):
            self.search_input.blockSignals(
                True
            )

            self.search_input.clear()

            self.search_input.blockSignals(
                False
            )

            self.editor.clear_search()

            self.search_count_label.clear()

        self.file_name_label.setText(
            "No file selected"
        )

        self.file_type_label.setText(
            ""
        )

        self.path_label.setText(
            "No file selected"
        )

        self.status_label.setText(
            "No file selected"
        )

        self.save_btn.setEnabled(
            False
        )

        self.restore_btn.setEnabled(
            False
        )

        self.highlighter.set_file_type(
            ""
        )

    # ==============================================================
    # Errors
    # ==============================================================

    def _show_error(
        self,
        title,
        message,
    ):
        QMessageBox.warning(
            self,
            title,
            str(message),
        )

        self.status_label.setText(
            "Error"
        )
