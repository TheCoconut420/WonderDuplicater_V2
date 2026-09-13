
import sys
import os

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QRegExp, QTimer, QModelIndex, QSize
from PyQt5.QtGui import (
    QFont, QRegExpValidator, QStandardItemModel, QStandardItem,
    QKeySequence, QDragEnterEvent, QDropEvent, QTextCursor, QColor
)

from SettingsManager import load_settings, save_settings, load_texts, SettingsTab
from FileEditor import PackEditorWidget, SingleFileEditor
from Duplication import CloneTab
from ImportActor import ImportActorTab
from ActorWizard import ActorWizardWidget


class LogEntry(QWidget):
    # shows one colored log entry
    COLORS = {"success": "#43a047", "error": "#e53935", "warning": "#f9a825", "info": "#757575"}

    def __init__(self, message, status="info", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 4, 1)
        bar = QFrame()
        bar.setFixedWidth(5)
        bar.setStyleSheet(f"background-color: {self.COLORS.get(status, self.COLORS['info'])};")
        label = QLabel(message)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(bar)
        layout.addWidget(label, 1)
        self.setToolTip(message)
        self.status = status


class StatusLog(QWidget):
    # displays filterable operation logs
    def __init__(self, texts, parent=None):
        super().__init__(parent)
        self.entries = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.errors_only = QCheckBox(texts.get("log_errors_only", "Show errors only"))
        self.warnings_only = QCheckBox(texts.get("log_warnings_only", "Show warnings only"))
        self.copy_button = QPushButton(texts.get("log_copy", "Copy log"))
        self.errors_only.toggled.connect(self.refresh)
        self.warnings_only.toggled.connect(self.refresh)
        self.copy_button.clicked.connect(self.copy_log)
        controls.addWidget(self.errors_only)
        controls.addWidget(self.warnings_only)
        controls.addStretch()
        controls.addWidget(self.copy_button)
        layout.addLayout(controls)
        self.list = QListWidget()
        self.list.itemClicked.connect(self.show_details)
        layout.addWidget(self.list)

    def add(self, message, status="info"):
        self.entries.append((message, status))
        self.refresh()

    def refresh(self):
        self.list.clear()
        for message, status in self.entries:
            if self.errors_only.isChecked() and status != "error":
                continue
            if self.warnings_only.isChecked() and status != "warning":
                continue
            item = QListWidgetItem()
            item.setData(Qt.UserRole, message)
            item.setSizeHint(QSize(0, 34))
            self.list.addItem(item)
            self.list.setItemWidget(item, LogEntry(message, status))
        self.list.scrollToBottom()

    def show_details(self, item):
        message = item.data(Qt.UserRole)
        QApplication.clipboard().setText(message)

    def copy_log(self):
        QApplication.clipboard().setText("\n".join(message for message, _ in self.entries))

    def refresh_texts(self, texts):
        """Refresh localized controls."""
        self.errors_only.setText(texts.get("log_errors_only", "Show errors only"))
        self.warnings_only.setText(texts.get("log_warnings_only", "Show warnings only"))
        self.copy_button.setText(texts.get("log_copy", "Copy log"))


# Implementation details.
class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.lang = self.settings["language"]
        self.texts = load_texts(self.lang)

        self.setWindowTitle(self.texts.get("title", "Mario Wonder Combi Tool"))
        geo = self.settings["window_geometry"]
        if "x" in geo:
            w, h = map(int, geo.split("x"))
        else:
            w, h = 1100, 750
        self.resize(w, h)

        self.setAcceptDrops(True)

        self.main_splitter = QSplitter(Qt.Vertical)
        self.setCentralWidget(self.main_splitter)

        self.tabs = QTabWidget()
        self.tabs.setMovable(True)
        self.tabs.tabBar().tabMoved.connect(self.on_tab_moved)
        self.main_splitter.addWidget(self.tabs)

        console_container = QWidget()
        console_layout = QVBoxLayout(console_container)
        console_layout.setContentsMargins(0,0,0,0)
        self.console = StatusLog(self.texts)
        console_layout.addWidget(self.console)

        self.main_splitter.addWidget(console_container)

        if "main_splitter" in self.settings:
            self.main_splitter.setSizes(self.settings["main_splitter"])
        self.main_splitter.splitterMoved.connect(self.save_main_splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(self.texts.get("status_ready", "Bereit"))

        # Tabs
        self.editor_tab = PackEditorWidget(self.texts)
        self.clone_tab = CloneTab(self.settings, self.texts, self.log, self.set_progress, self.editor_tab, self)
        self.import_actor_tab = ImportActorTab(self.settings, self.texts, self.log, self)
        self.settings_tab = SettingsTab(self.settings, self.texts, self)

        # Actor-Wizard-Tab (enthält jetzt Hitbox-Editor und Damage etc.)
        try:
            self.actor_wizard_tab = ActorWizardWidget(self.settings, self.texts, self.log, on_actor_changed=self.refresh_all_directories)
        except Exception as e:
            self.actor_wizard_tab = None
            self.log(self.texts.get("error_loading_actor_wizard", "Could not load Actor Wizard: {0}").format(str(e)), "error")

        self.tab_widgets = {
            "editor": self.editor_tab,
            "clone": self.clone_tab,
            "import_actor": self.import_actor_tab,
            "settings": self.settings_tab,
        }
        if self.actor_wizard_tab is not None:
            self.tab_widgets["actor_wizard"] = self.actor_wizard_tab

        self.tab_names = {
            "editor": self.texts.get("tab_editor", "Pack Editor"),
            "clone": self.texts.get("tab_clone", "Clone Actor"),
            "import_actor": self.texts.get("tab_import_actor", "Import Actor"),
            "settings": self.texts.get("tab_settings", "Settings"),
            "actor_wizard": self.texts.get("tab_actor_wizard", "Actor Wizard"),
        }
        order = self.settings.get("tab_order", ["clone", "import_actor", "editor", "settings", "actor_wizard"])
        for key in order:
            if key in self.tab_widgets:
                self.tabs.addTab(self.tab_widgets[key], self.tab_names.get(key, key))

        self.apply_theme()
        self.apply_font()

        for p in self.settings.get("open_editor_tabs", []):
            self.editor_tab.load_file(p)

        QShortcut(QKeySequence("Ctrl+O"), self, self.editor_tab.open_file)
        QShortcut(QKeySequence("Ctrl+S"), self, self.save_current_editor)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self.save_current_editor_as)

        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.save_window_geometry)
        self.resizing = False

    def tr(self, key, *args):
        """Return a localized string."""
        return self.texts.get(key, key).format(*args)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resizing = True
        self.resize_timer.start(500)

    def save_window_geometry(self):
        if self.resizing:
            w = self.width()
            h = self.height()
            self.settings["window_geometry"] = f"{w}x{h}"
            save_settings(self.settings)
            self.resizing = False

    def log(self, msg, status="info"):
        self.console.add(str(msg), status)

    def set_progress(self, value):
        pass  # Fortschrittsbalken wurde entfernt

    def refresh_all_directories(self):
        """Implementation details."""
        if hasattr(self, "clone_tab") and self.clone_tab is not None:
            self.clone_tab.load_actors()
        if hasattr(self, "editor_tab") and self.editor_tab is not None and hasattr(self.editor_tab, "on_source_changed"):
            self.editor_tab.on_source_changed()
        if getattr(self, "actor_wizard_tab", None) is not None:
            self.actor_wizard_tab.load_actors()
        if hasattr(self, "import_actor_tab") and self.import_actor_tab is not None:
            self.import_actor_tab.refresh_paths()

    def save_current_editor(self):
        current_widget = self.editor_tab.inner_tabs.currentWidget()
        if isinstance(current_widget, SingleFileEditor):
            current_widget.save()

    def save_current_editor_as(self):
        current_widget = self.editor_tab.inner_tabs.currentWidget()
        if isinstance(current_widget, SingleFileEditor):
            current_widget.save_as()

    def save_main_splitter(self):
        self.settings["main_splitter"] = self.main_splitter.sizes()
        save_settings(self.settings)

    def open_path_in_editor(self, path):
        if os.path.isfile(path):
            self.editor_tab.load_file(path)
            self.tabs.setCurrentWidget(self.editor_tab)

    def apply_font(self):
        family = self.settings["font_family"]
        size = self.settings["font_size"]
        app_font = QFont(family, size)
        QApplication.setFont(app_font)
        for widget in QApplication.allWidgets():
            if not widget.objectName() == "monoEditor":
                widget.setFont(app_font)
        for widget in QApplication.allWidgets():
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def apply_theme(self):
        if self.settings["theme"] == "dark":
            self.setStyleSheet("""
                QWidget { background-color: #2e2e2e; color: white; }
                QLineEdit, QTextEdit, QPlainTextEdit, QListWidget, QTreeView, QComboBox {
                    background-color: #3c3c3c; color: white; border: 1px solid #5a5a5a;
                }
                QGroupBox { color: white; border: 1px solid #5a5a5a; margin-top: 1em; }
                QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
                QPushButton { background-color: #424242; color: white; border: 1px solid #5a5a5a; padding: 5px; }
                QPushButton:hover { background-color: #505050; }
                QPushButton:disabled { background-color: #555; color: #aaa; }
                QHeaderView::section { background-color: #3c3c3c; color: white; border: 1px solid #5a5a5a; padding: 4px; }
                QCheckBox { color: white; }
                QListWidget::item:selected { background-color: #388e3c; color: white; }
                QTabWidget::pane { border: 1px solid #5a5a5a; }
                QTabBar::tab { background-color: #3c3c3c; color: white; padding: 6px; }
                QTabBar::tab:selected { background-color: #505050; }
                QToolTip { background-color: #424242; color: white; border: 1px solid #5a5a5a; }
            """)
        else:
            self.setStyleSheet("")

    def apply_theme_and_language(self):
        """Apply the selected theme and translations."""
        self.lang = self.settings["language"]
        self.texts = load_texts(self.lang)
        self.setWindowTitle(self.tr("title"))
        self.status_bar.showMessage(self.tr("status_ready"))
        self.console.refresh_texts(self.texts)
        self.clone_tab.refresh_texts(self.texts)
        self.import_actor_tab.refresh_texts(self.texts)
        self.editor_tab.refresh_texts(self.texts)
        self.settings_tab.refresh_texts(self.texts)
        if self.actor_wizard_tab is not None:
            self.actor_wizard_tab.refresh_texts(self.texts)
        self.tab_names = {
            "editor": self.tr("tab_editor"),
            "clone": self.tr("tab_clone"),
            "import_actor": self.tr("tab_import_actor"),
            "settings": self.tr("tab_settings"),
            "actor_wizard": self.tr("tab_actor_wizard"),
        }
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            for key, tab_widget in self.tab_widgets.items():
                if widget is tab_widget:
                    self.tabs.setTabText(index, self.tab_names[key])
                    break
        self.apply_theme()

    def on_tab_moved(self, _from_index, _to_index):
        """Persist the current tab order."""
        reverse = {widget: key for key, widget in self.tab_widgets.items()}
        self.settings["tab_order"] = [reverse[self.tabs.widget(index)] for index in range(self.tabs.count())]
        save_settings(self.settings)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                self.editor_tab.load_file(path)
                self.tabs.setCurrentWidget(self.editor_tab)

    def closeEvent(self, event):
        """Prompt before closing unsaved editor tabs."""
        for editor in self.editor_tab.open_docs.values():
            if getattr(editor, "modified", False):
                result = QMessageBox.question(
                    self,
                    self.tr("unsaved_changes_title"),
                    self.tr("unsaved_changes_text"),
                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                )
                if result == QMessageBox.Cancel:
                    event.ignore()
                    return
                if result == QMessageBox.Save:
                    for open_editor in self.editor_tab.open_docs.values():
                        if getattr(open_editor, "modified", False):
                            open_editor.save()
                break
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow(load_settings())
    window.show()
    sys.exit(app.exec_())
