"""Small native GUI. All network/service work runs off the Qt UI thread."""
from __future__ import annotations
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QTabWidget, QPlainTextEdit,
    QFileDialog, QInputDialog, QLineEdit, QMessageBox, QComboBox, QCheckBox,
    QSpinBox, QSplitter, QGroupBox, QMenu,
)
from .controller import Controller
from .diagnostics import report, safe_logs
from .mihomo_api import API
from .models import Settings
from .network import external_ip
from .recovery import return_to_verge
from .redaction import redact, redact_url
from .storage import ControllerError, Paths, write_json


class Job(QThread):
    result = Signal(object)

    def __init__(self, paths, operation, parent=None):
        super().__init__(parent)
        self.paths, self.operation = paths, operation

    def run(self):
        try:
            with self.paths.lock():
                value = self.operation(Controller(self.paths))
            self.result.emit((True, value))
        except Exception as error:
            message = str(error) if isinstance(error, ControllerError) else f'{type(error).__name__}: operation failed. Check settings, file access and service status.'
            self.result.emit((False, redact(message)))


class Window(QMainWindow):
    def __init__(self, paths=None, auto_refresh=True):
        super().__init__()
        self.paths = paths or Paths()
        self.job = None
        self.callback = None
        self.pending_result = None
        self.profile_data = {}
        self.group_data = {}
        self.inputs = {}
        self.loaded_settings = False
        self.runtime_active = False
        self.watchdog_stopped = False
        self.setWindowTitle('Minimal Mihomo Controller')
        self.resize(1050, 760)
        self.setMinimumSize(860, 620)
        root = QWidget()
        outer = QVBoxLayout(root)
        title = QLabel('Minimal Mihomo Controller')
        title.setStyleSheet('font-size: 23px; font-weight: 600;')
        outer.addWidget(title)
        self.banner = QLabel('Checking local service…')
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet('padding: 12px; background: #293746; color: white; border-radius: 6px;')
        outer.addWidget(self.banner)
        split = QSplitter()
        outer.addWidget(split, 1)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.addWidget(QLabel('PROFILES'))
        self.profiles = QListWidget()
        self.profiles.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.profiles.customContextMenuRequested.connect(self.profile_menu)
        self.profiles.itemClicked.connect(self.profile_clicked)
        side.addWidget(self.profiles, 1)
        for label, callback in [('Add YAML…', self.add_yaml), ('Add subscription…', self.add_subscription),
                                ('Refresh subscription', self.refresh_subscription),
                                ('Duplicate metadata…', self.duplicate), ('Remove profile', self.remove)]:
            self.button(side, label, callback)
        split.addWidget(sidebar)
        self.tabs = QTabWidget()
        split.addWidget(self.tabs)
        split.setSizes([260, 740])
        self.make_overview()
        self.make_proxies()
        self.make_settings()
        self.subscriptions = self.text_tab('Subscriptions')
        self.logs = self.text_tab('Logs')
        self.diagnostics = self.text_tab('Diagnostics')
        bottom = QHBoxLayout()
        self.button(bottom, 'Refresh status', self.refresh)
        self.button(bottom, 'Copy diagnostics', lambda: QApplication.clipboard().setText(self.diagnostics.toPlainText()))
        self.button(bottom, 'Return to Clash Verge', self.return_to_verge)
        outer.addLayout(bottom)
        self.setCentralWidget(root)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(10000)
        self.watchdog.timeout.connect(self.check_proxy_health)
        if auto_refresh:
            self.timer.start()
            self.watchdog.start()
            self.refresh()

    @staticmethod
    def button(layout, label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def text_tab(self, title):
        text = QPlainTextEdit()
        text.setReadOnly(True)
        self.tabs.addTab(text, title)
        return text

    def make_overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.summary = QLabel('No runtime verified yet.')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setStyleSheet('font-size: 17px; padding: 10px;')
        layout.addWidget(self.summary)
        note = QLabel('Quit Clash Verge and its core before applying a TUN profile.\n'
                      'Source YAML is never edited. A failed apply restores this controller’s last-good configuration, when one exists.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.health = QCheckBox('Require an HTTPS health check through the mixed proxy when applying')
        self.health.setChecked(True)
        layout.addWidget(self.health)
        controls = QHBoxLayout()
        self.proxy_toggle = QPushButton('Proxy is OFF')
        self.proxy_toggle.setCheckable(True)
        self.proxy_toggle.setMinimumHeight(42)
        self.proxy_toggle.clicked.connect(self.toggle_proxy)
        controls.addWidget(self.proxy_toggle)
        controls.addWidget(QLabel('Mode'))
        self.inputs['mode'] = QComboBox()
        self.inputs['mode'].addItems(['rule', 'global', 'direct'])
        # Only user activation fires this signal; programmatic refreshes do not.
        self.inputs['mode'].activated.connect(lambda _index: self.apply())
        controls.addWidget(self.inputs['mode'])
        layout.addLayout(controls)
        self.autostart = QCheckBox('Start proxy automatically when the computer starts')
        self.autostart.setEnabled(False)
        self.autostart.setToolTip('Configured by the installer. Use systemctl enable/disable minimal-mihomo.service to change it.')
        layout.addWidget(self.autostart)
        self.button(layout, 'Apply / reconnect selected profile', self.apply)
        row = QHBoxLayout()
        self.button(row, 'Start saved runtime', lambda: self.perform(lambda c: c.start()))
        self.button(row, 'Stop our core', lambda: self.perform(lambda c: c.service.action('stop')))
        self.button(row, 'Restart saved runtime', lambda: self.perform(lambda c: c.start(True)))
        layout.addLayout(row)
        row = QHBoxLayout()
        self.button(row, 'Restore last good', lambda: self.perform(lambda c: c.rollback()))
        self.button(row, 'Recover interrupted apply', lambda: self.perform(lambda c: c.recover()))
        self.button(row, 'Compare direct and proxy IP', self.check_external)
        layout.addLayout(row)
        self.external = QPlainTextEdit()
        self.external.setReadOnly(True)
        self.external.setPlaceholderText('Shows both the physical/direct route and a request forced through Mihomo.')
        layout.addWidget(self.external, 1)
        self.tabs.addTab(page, 'Overview')

    def make_proxies(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.groups = QComboBox()
        self.nodes = QComboBox()
        self.current_node = QLabel('No running proxy groups loaded.')
        self.current_node.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(QLabel('Group'))
        layout.addWidget(self.groups)
        layout.addWidget(self.current_node)
        layout.addWidget(self.nodes)
        self.groups.currentTextChanged.connect(self.group_changed)
        self.button(layout, 'Select node', self.select_node)
        self.button(layout, 'Test node latency', self.latency)
        self.button(layout, 'Test all available nodes', self.latency_all)
        self.latency_result = QLabel('')
        layout.addWidget(self.latency_result)
        self.latency_results = QPlainTextEdit()
        self.latency_results.setReadOnly(True)
        self.latency_results.setPlaceholderText('All-node test results will appear here, fastest first.')
        layout.addWidget(self.latency_results, 1)
        layout.addStretch()
        self.tabs.addTab(page, 'Proxies')

    def make_settings(self):
        page = QWidget()
        form = QFormLayout(page)
        for key, label in [('tun', 'TUN enabled'), ('auto_route', 'Automatic route'),
                           ('auto_detect_interface', 'Detect physical interface'),
                           ('auto_redirect', 'Automatic redirect'), ('strict_route', 'Strict route'),
                           ('secure_dns', 'Secure DNS through routing rules (recommended)')]:
            widget = QCheckBox()
            self.inputs[key] = widget
            form.addRow(label, widget)
        for key, label, choices in [('stack', 'TUN stack', ['gvisor', 'system', 'mixed']),
                                    ('ipv6', 'Mihomo IPv6', ['Preserve source', 'Enabled', 'Disabled'])]:
            widget = QComboBox()
            widget.addItems(choices)
            self.inputs[key] = widget
            form.addRow(label, widget)
        for key, label in [('interface', 'Physical interface override'), ('dns_hijack', 'DNS hijack (comma separated)'),
                           ('route_exclude_address', 'Route exclusions (comma separated CIDRs)')]:
            widget = QLineEdit()
            self.inputs[key] = widget
            form.addRow(label, widget)
        for key, label, low, high in [('mtu', 'MTU', 1280, 9000), ('mixed_port', 'Mixed port', 1024, 65535),
                                      ('api_port', 'API port', 1024, 65535)]:
            widget = QSpinBox()
            widget.setRange(low, high)
            self.inputs[key] = widget
            form.addRow(label, widget)
        note = QLabel('Save records desired settings. Apply selected profile activates them.\n'
                      'IPv6 disabled here does not disable Ubuntu IPv6. Source DNS and rules are preserved.')
        note.setWordWrap(True)
        form.addRow(note)
        save = QPushButton('Save desired settings')
        save.clicked.connect(self.save_settings)
        form.addRow(save)
        self.tabs.addTab(page, 'TUN & Mode')

    def desired_values(self):
        values = {}
        for key, widget in self.inputs.items():
            if isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                values[key] = widget.value()
            elif isinstance(widget, QComboBox):
                values[key] = [None, True, False][widget.currentIndex()] if key == 'ipv6' else widget.currentText()
            else:
                value = widget.text().strip()
                values[key] = [v.strip() for v in value.split(',') if v.strip()] if key in ('dns_hijack', 'route_exclude_address') else value
        return values

    @staticmethod
    def store_settings(controller, values):
        settings = Settings.load(controller.paths)
        for key, value in values.items():
            setattr(settings, key, value)
        settings.validate()
        write_json(controller.paths.settings, asdict(settings))

    def save_settings(self):
        if not self.loaded_settings:
            return
        values = self.desired_values()
        self.perform(lambda c: self.store_settings(c, values))

    def selected_id(self):
        item = self.profiles.currentItem()
        if item is None:
            self.statusBar().showMessage('Select a profile first.')
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def apply(self):
        if not self.loaded_settings:
            return
        profile_id = self.selected_id()
        if not profile_id:
            return
        values = self.desired_values()
        health_url = 'https://www.gstatic.com/generate_204' if self.health.isChecked() else None
        def operation(controller):
            self.store_settings(controller, values)
            controller.apply(profile_id, health_url)
        self.perform(operation)

    def add_yaml(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Add source YAML (read-only)', str(Path.home() / 'Downloads'), 'YAML (*.yaml *.yml)')
        if path:
            self.perform(lambda c: c.profiles.add_yaml(Path(path)))

    def add_subscription(self):
        name, ok = QInputDialog.getText(self, 'Add subscription', 'Profile name:')
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, 'Add subscription', 'HTTPS subscription URL:', QLineEdit.EchoMode.Password)
        if ok and url.strip():
            self.perform(lambda c: c.profiles.add_subscription(url.strip(), c.candidate, name.strip()))

    def rename(self):
        profile_id = self.selected_id()
        if not profile_id:
            return
        current = self.profile_data.get(profile_id, {}).get('name', '')
        name, ok = QInputDialog.getText(self, 'Rename profile', 'New profile name:', text=current)
        if ok and name.strip():
            self.perform(lambda c: c.profiles.rename(profile_id, name))

    def profile_menu(self, position):
        item = self.profiles.itemAt(position)
        if item is None:
            return
        self.profiles.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('Apply / reconnect', self.apply)
        menu.addAction('Rename…', self.rename)
        if self.profile_data.get(self.selected_id(), {}).get('kind') == 'subscription':
            menu.addAction('Refresh subscription', self.refresh_subscription)
        menu.addAction('Duplicate…', self.duplicate)
        menu.addAction('Open profile settings', lambda: self.tabs.setCurrentIndex(2))
        menu.addSeparator()
        menu.addAction('Remove…', self.remove)
        menu.exec(self.profiles.mapToGlobal(position))

    def profile_clicked(self, _item):
        self.apply()

    def refresh_subscription(self):
        profile_id = self.selected_id()
        if profile_id:
            self.perform(lambda c: c.profiles.refresh(profile_id, c.candidate))

    def duplicate(self):
        profile_id = self.selected_id()
        if not profile_id:
            return
        name, ok = QInputDialog.getText(self, 'Duplicate metadata', 'New profile name:')
        if ok and name.strip():
            self.perform(lambda c: c.profiles.duplicate(profile_id, name.strip()))

    def remove(self):
        profile_id = self.selected_id()
        if profile_id and QMessageBox.question(self, 'Remove profile', 'Remove this profile’s metadata? The source file and cached snapshots remain.') == QMessageBox.StandardButton.Yes:
            self.perform(lambda c: c.profiles.remove(profile_id))

    def return_to_verge(self):
        self.perform(lambda c: return_to_verge(c.service), lambda result: QMessageBox.information(self, 'Clash Verge recovery', result))

    def select_node(self):
        group, node = self.groups.currentText(), self.nodes.currentText()
        if group and node:
            self.perform(lambda c: API(c.config()).select(group, node))

    def latency(self):
        node = self.nodes.currentText()
        if node:
            self.perform(lambda c: API(c.config()).latency(node), lambda r: self.latency_result.setText(f"Latency: {r.get('delay', 'unknown')} ms"))

    def latency_all(self):
        nodes = [node for group in self.group_data.values() for node in group.get('all', [])
                 if node not in ('DIRECT', 'REJECT', 'REJECT-DROP', 'PASS')]
        if not nodes:
            self.statusBar().showMessage('No proxy nodes are available in the running profile.')
            return
        def show(results):
            ordered = sorted(results.items(), key=lambda item: item[1] if isinstance(item[1], int) else 10**9)
            self.latency_results.setPlainText('\n'.join(
                f'{delay:>5} ms   {name}' if isinstance(delay, int) else f'  FAIL      {name}'
                for name, delay in ordered))
        self.perform(lambda c: API(c.config()).latencies(nodes), show)

    def toggle_proxy(self, enabled):
        if enabled:
            profile_id = self.selected_id()
            if profile_id:
                self.apply()
            else:
                self.perform(lambda c: c.start())
        else:
            self.perform(lambda c: c.service.action('stop'))

    def check_external(self):
        def operation(c):
            try:
                config = c.config()
            except ControllerError:
                config = None
            return external_ip(config)
        self.perform(operation, self.show_external)

    def check_proxy_health(self):
        if not self.runtime_active or self.job is not None:
            return
        self.perform(lambda c: c.monitor_or_stop(30), self.watchdog_result, quiet=True)

    def watchdog_result(self, result):
        if not result.get('stopped'):
            self.watchdog_stopped = False
            return
        if self.watchdog_stopped:
            return
        self.watchdog_stopped = True
        profile = result.get('profile', 'current profile')
        node = result.get('node') or profile
        seconds = result.get('timeout', 30)
        message = (f'Current node “{node}” (profile “{profile}”) could not connect for {seconds} seconds.\n\n'
                   'Proxy has been turned OFF automatically so direct networking can recover.')
        self.banner.setText(message)
        self.banner.setStyleSheet('padding: 12px; background: #8c302e; color: white; border-radius: 6px;')
        QMessageBox.warning(self, 'Proxy node unavailable — turned off', message)

    def group_changed(self, name):
        item = self.group_data.get(name, {})
        self.nodes.clear()
        self.nodes.addItems(item.get('all', []))
        self.nodes.setCurrentText(item.get('now') or '')
        self.current_node.setText('Current: ' + str(item.get('now') or 'not selected'))

    def show_external(self, result):
        self.external.setPlainText(json.dumps(result, indent=2))

    def perform(self, operation, callback=None, quiet=False):
        if self.job is not None:
            if not quiet:
                self.statusBar().showMessage('An operation is already running. Please wait.')
            return
        self.callback = callback
        self.quiet_job = quiet
        self.pending_result = None
        self.statusBar().showMessage('Working…')
        self.job = Job(self.paths, operation, self)
        self.job.result.connect(self.got_result)
        self.job.finished.connect(self.job_finished)
        self.job.start()

    @Slot(object)
    def got_result(self, result):
        self.pending_result = result

    @Slot()
    def job_finished(self):
        job, callback, result = self.job, self.callback, self.pending_result
        self.job = None
        job.deleteLater()
        if result is None:
            self.statusBar().showMessage('Operation ended without a result.')
            return
        ok, value = result
        if ok:
            self.statusBar().showMessage('Ready')
            if callback:
                callback(value)
            if callback != self.render:
                self.refresh()
        else:
            self.statusBar().showMessage(value)
            self.banner.setText(value)
            self.banner.setStyleSheet('padding: 12px; background: #8c302e; color: white; border-radius: 6px;')
            if not self.quiet_job:
                QMessageBox.warning(self, 'Operation failed', value)

    def refresh(self):
        def operation(c):
            settings = asdict(Settings.load(c.paths))
            settings.pop('api_secret', None)
            profiles = c.profiles.all()
            for profile in profiles.values():
                if 'url' in profile:
                    profile['url'] = redact_url(profile['url'])
            return {'settings': settings, 'profiles': profiles, 'report': report(c), 'logs': safe_logs(c, 100),
                    'autostart': c.service.enabled()}
        self.perform(operation, self.render, quiet=True)

    def render(self, data):
        selected = self.selected_id() if self.profiles.currentItem() else None
        self.profile_data = data['profiles']
        self.profiles.clear()
        for profile_id, profile in self.profile_data.items():
            item = QListWidgetItem(profile['name'] + ('  · subscription' if profile['kind'] == 'subscription' else ''))
            item.setData(Qt.ItemDataRole.UserRole, profile_id)
            item.setToolTip(profile.get('url') or profile['path'])
            self.profiles.addItem(item)
            if profile_id == selected:
                self.profiles.setCurrentItem(item)
        if not self.profiles.currentItem() and self.profiles.count():
            self.profiles.setCurrentRow(0)
        if not self.loaded_settings:
            for key, widget in self.inputs.items():
                value = data['settings'][key]
                if isinstance(widget, QCheckBox):
                    widget.setChecked(value)
                elif isinstance(widget, QSpinBox):
                    widget.setValue(value)
                elif isinstance(widget, QComboBox):
                    if key == 'ipv6':
                        widget.setCurrentIndex(0 if value is None else 1 if value else 2)
                    else:
                        widget.setCurrentText(value)
                else:
                    widget.setText(', '.join(value) if isinstance(value, list) else value)
            self.loaded_settings = True
        r = data['report']
        state = r['service'].get('ActiveState', 'unknown')
        active = state == 'active'
        self.runtime_active = active
        self.proxy_toggle.blockSignals(True)
        self.proxy_toggle.setChecked(active)
        self.proxy_toggle.setText('Proxy is ON' if active else 'Proxy is OFF')
        self.proxy_toggle.setStyleSheet('font-weight: 700; padding: 8px; background: ' + ('#2f7d4a' if active else '#7a3434') + '; color: white;')
        self.proxy_toggle.blockSignals(False)
        self.autostart.blockSignals(True)
        self.autostart.setChecked(bool(data.get('autostart')))
        self.autostart.blockSignals(False)
        desired = 'ON' if r['desired_tun'] else 'OFF'
        runtime = 'ON' if r.get('runtime_tun') is True else 'OFF' if r.get('runtime_tun') is False else 'UNKNOWN'
        self.summary.setText(f"Service: {state}   ·   PID: {r['service'].get('MainPID', 'unknown')}\n"
                             f"Desired TUN: {desired}     Runtime TUN: {runtime}\n"
                             f"Desired mode: {r['desired_mode']}     Runtime mode: {r.get('runtime_mode', 'unknown')}\n"
                             f"Active profile: {r.get('profile_name') or 'None'}")
        if r.get('state_mismatch') or r.get('interrupted_transaction'):
            message = r.get('error') or 'An interrupted apply needs recovery.'
            color = '#8c302e'
        elif state == 'active' and r.get('applied_tun') and not r.get('tun_interface_exists'):
            message = 'TUN FAILURE: our runtime requests TUN, but the Mihomo interface is absent.'
            color = '#8c302e'
        elif r.get('other_processes'):
            message = 'Clash/Mihomo is running separately. Your current connection is unchanged. Stop that core before applying TUN here.'
            color = '#765116'
        elif r.get('error') or r.get('state_mismatch') or r.get('interrupted_transaction'):
            message = r.get('error') or 'An interrupted apply needs recovery.'
            color = '#8c302e'
        elif state == 'active':
            message = 'Our service is active. Check External IP before relying on the connection.'
            color = '#23583e'
        else:
            message = 'Our service is stopped. Select a profile to prepare a test.'
            color = '#293746'
        self.banner.setText(message)
        self.banner.setStyleSheet(f'padding: 12px; background: {color}; color: white; border-radius: 6px;')
        group = self.groups.currentText()
        node = self.nodes.currentText()
        self.group_data = r.get('proxy_groups', {})
        self.groups.blockSignals(True)
        self.groups.clear()
        self.groups.addItems(list(self.group_data))
        if group in self.group_data:
            self.groups.setCurrentText(group)
        self.groups.blockSignals(False)
        self.group_changed(self.groups.currentText())
        if group == self.groups.currentText() and node in self.group_data.get(group, {}).get('all', []):
            self.nodes.setCurrentText(node)
        self.diagnostics.setPlainText(redact(json.dumps(r, indent=2)))
        self.logs.setPlainText(data['logs'])
        self.subscriptions.setPlainText(json.dumps({k: v for k, v in self.profile_data.items() if v['kind'] == 'subscription'}, indent=2))

    def closeEvent(self, event):
        if self.job is not None:
            self.statusBar().showMessage('Wait for the current operation before closing. Closing the window does not stop the core.')
            event.ignore()
        else:
            event.accept()


def main():
    if os.geteuid() == 0:
        print('Run the GUI as your desktop user, not root.', file=sys.stderr)
        return 1
    os.umask(0o077)
    app = QApplication(sys.argv)
    app.setApplicationName('Minimal Mihomo Controller')
    app.setStyleSheet('QWidget { font-size: 16px; } QTabBar::tab { padding: 9px 14px; } '
                      'QPushButton { padding: 7px 10px; } QComboBox, QLineEdit, QSpinBox { min-height: 30px; } '
                      'QListWidget::item { padding: 8px; }')
    window = Window()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
