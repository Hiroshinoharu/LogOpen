"""Exercise desktop scanning with real Qt threads and a mocked scan function."""

import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import sip
    from PyQt6.QtCore import QPoint, QThread, QTimer, Qt, pyqtSlot
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QAbstractItemView, QApplication, QTableWidgetItem
except ModuleNotFoundError as exc:
    if exc.name != "PyQt6":
        raise
    raise unittest.SkipTest("PyQt6 is required for the desktop UI tests") from exc

from errors import EventCollectionError, ReportExportError
from ui.main_window import MainWindow


class RecordingWindow(MainWindow):
    """Record which thread runs each UI slot without changing signal delivery."""

    def __init__(self):
        super().__init__()
        self.slot_threads = []
        self.running_during_cleanup = []

    @pyqtSlot(list)
    def _show_scan_results(self, incidents):
        self.slot_threads.append(("success", QThread.currentThread()))
        super()._show_scan_results(incidents)

    @pyqtSlot(str)
    def _show_scan_error(self, message):
        self.slot_threads.append(("error", QThread.currentThread()))
        super()._show_scan_error(message)

    @pyqtSlot()
    def _scan_finished(self):
        self.slot_threads.append(("finished", QThread.currentThread()))
        self.running_during_cleanup.append(self._scan_thread.isRunning())
        super()._scan_finished()

    @pyqtSlot()
    def _open_incident_detail(self):
        self.slot_threads.append(("detail", QThread.currentThread()))
        super()._open_incident_detail()


class MainWindowThreadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.window = RecordingWindow()
        self.release_scan = threading.Event()

    def tearDown(self):
        # Unblock the fake scan even if an assertion fails before normal cleanup.
        self.release_scan.set()
        self.wait_until(lambda: self.window._scan_thread is None)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Timed out waiting for Qt scan activity")

    def finish_scan(self, worker, thread):
        self.wait_until(lambda: self.window._scan_thread is None)
        self.wait_until(lambda: sip.isdeleted(worker) and sip.isdeleted(thread))
        self.assertIsNone(self.window._scan_worker)
        self.assertTrue(self.window.scan_button.isEnabled())
        self.assertFalse(any(self.window.running_during_cleanup))
        self.assertTrue(
            all(thread == self.app.thread() for _, thread in self.window.slot_threads)
        )

    def test_scan_keeps_ui_responsive_and_prevents_overlapping_scans(self):
        started = threading.Event()
        scan_threads = []
        timer_ticks = []
        incidents = [
            {
                "incident_priority": "High",
                "incident_classification": "DNS Client",
                "incident_score": 65,
            }
        ]

        def fake_scan():
            scan_threads.append(QThread.currentThread())
            started.set()
            if not self.release_scan.wait(5):
                raise RuntimeError("Test did not release the scan")
            return incidents

        self.window.incident_table.setRowCount(1)
        self.window.incident_table.setItem(0, 1, QTableWidgetItem("Previous results"))
        with patch("ui.main_window.scan_system", side_effect=fake_scan) as scan:
            self.window.scan_button.click()
            worker, thread = self.window._scan_worker, self.window._scan_thread
            self.assertFalse(self.window.scan_button.isEnabled())
            self.assertEqual(self.window.status_label.text(), "Scanning system...")
            self.assertEqual(self.window.incident_table.rowCount(), 0)
            self.wait_until(started.is_set)

            # A UI timer must run while the background scan is still blocked.
            QTimer.singleShot(0, lambda: timer_ticks.append(True))
            self.wait_until(lambda: bool(timer_ticks))
            self.assertFalse(self.release_scan.is_set())
            self.assertNotEqual(scan_threads[0], self.app.thread())

            self.window.run_scan()
            self.window.scan_button.click()
            self.assertIs(self.window._scan_thread, thread)
            scan.assert_called_once_with()

            self.release_scan.set()
            self.finish_scan(worker, thread)

        table = self.window.incident_table
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "High")
        self.assertEqual(table.item(0, 1).text(), "DNS Client")
        self.assertEqual(table.item(0, 2).text(), "65 / 100")
        self.assertEqual(self.window.incident_total.text(), "1")
        self.assertEqual(self.window.attention_total.text(), "1")
        self.assertEqual(
            self.window.status_label.text(), "Scan complete - 1 incidents found"
        )
        self.assertEqual(
            [name for name, _ in self.window.slot_threads], ["success", "finished"]
        )

    def test_empty_results_and_repeated_scans_clean_up(self):
        with patch("ui.main_window.scan_system", return_value=[]) as scan:
            for _ in range(3):
                self.window.scan_button.click()
                self.finish_scan(self.window._scan_worker, self.window._scan_thread)
                self.assertEqual(
                    self.window.status_label.text(), "Scan complete - 0 incidents found"
                )
                self.assertEqual(self.window.incident_table.rowCount(), 0)
            self.assertEqual(scan.call_count, 3)

    def test_errors_are_readable_and_allow_another_scan(self):
        for error, message in (
            (EventCollectionError("access\n denied"), "access denied"),
            (ReportExportError(), "ReportExportError"),
            (AttributeError("unexpected failure"), "unexpected failure"),
            (KeyboardInterrupt(), "Scan interrupted."),
        ):
            with self.subTest(error=type(error).__name__), patch(
                "ui.main_window.scan_system", side_effect=error
            ):
                self.window.scan_button.click()
                self.finish_scan(self.window._scan_worker, self.window._scan_thread)
                self.assertEqual(
                    self.window.status_label.text(), f"Scan failed: {message}"
                )
                self.assertEqual(self.window.incident_table.rowCount(), 0)
                self.assertEqual(
                    [name for name, _ in self.window.slot_threads[-2:]],
                    ["error", "finished"],
                )

        with patch("ui.main_window.scan_system", return_value=[]):
            self.window.scan_button.click()
            self.finish_scan(self.window._scan_worker, self.window._scan_thread)
        self.assertEqual(
            self.window.status_label.text(), "Scan complete - 0 incidents found"
        )

    def check_close_during_scan(self, error=None):
        started = threading.Event()

        def fake_scan():
            started.set()
            if not self.release_scan.wait(5):
                raise RuntimeError("Test did not release the scan")
            if error is not None:
                raise error
            return []

        self.window.show()
        with patch("ui.main_window.scan_system", side_effect=fake_scan):
            self.window.scan_button.click()
            worker, thread = self.window._scan_worker, self.window._scan_thread
            self.wait_until(started.is_set)
            self.assertFalse(self.window.close())
            self.assertTrue(self.window.isVisible())
            self.assertTrue(thread.isRunning())
            self.assertIn("before closing", self.window.status_label.text())

            self.release_scan.set()
            self.finish_scan(worker, thread)
            self.assertFalse(self.window.isVisible())

    def test_close_waits_for_successful_scan_without_destroying_running_thread(self):
        self.check_close_during_scan()

    def test_close_waits_for_failed_scan_without_destroying_running_thread(self):
        self.check_close_during_scan(ReportExportError("could not write report"))

    def test_table_is_read_only_and_preserves_literal_incident_data(self):
        self.window._show_scan_results([{
            "incident_priority": "High",
            "incident_classification": "Service <example> & application",
            "incident_score": 65,
            "event_count": 4,
            "is_recurring": True,
            "log_type": "System",
            "providers": ["Provider <name>"],
            "summary_text": "Check <service> & retry.",
        }])
        table = self.window.incident_table
        self.assertEqual(table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
        self.assertEqual(table.item(0, 1).text(), "Service <example> & application")
        self.assertEqual(table.item(0, 3).text(), "Yes")
        self.assertEqual(table.item(0, 4).text(), "4")
        self.assertEqual(table.item(0, 1).toolTip(), "<qt>Service &lt;example&gt; &amp; application</qt>")
        table.selectRow(0)
        self.window.detail_button.click()
        self.assertEqual(self.window.incident_detail.fields["providers"].text(), "Provider <name>")
        self.assertEqual(self.window.incident_detail.summary.text(), "Check <service> & retry.")
        for column in range(table.columnCount()):
            self.assertFalse(table.item(0, column).flags() & Qt.ItemFlag.ItemIsEditable)

    def test_table_supports_keyboard_selection_copy_and_resizing(self):
        self.window.show()
        self.window._show_scan_results([
            {"incident_priority": "High", "incident_classification": "DNS Client",
             "incident_score": 65, "event_count": 4, "log_type": "System"},
            {"incident_priority": "Low", "incident_classification": "Application warning",
             "incident_score": 20, "event_count": 1, "log_type": "Application", "is_recurring": False},
        ])
        table = self.window.incident_table
        self.app.processEvents()
        table.setFocus()
        table.setCurrentCell(0, 0)
        QTest.keyClick(table, Qt.Key.Key_Down)
        self.assertEqual([index.row() for index in table.selectionModel().selectedRows()], [1])
        self.assertEqual(len(table.selectedItems()), table.columnCount())
        self.assertTrue(self.window.copy_action.isEnabled())
        QTest.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(
            self.app.clipboard().text(), "Low\tApplication warning\t20 / 100\tNo\t1"
        )

        widths = []
        for width, height in ((800, 620), (1280, 900)):
            self.window.resize(width, height)
            self.app.processEvents()
            classification_width = table.columnWidth(1)
            widths.append(classification_width)
            self.assertGreater(
                classification_width,
                max(table.columnWidth(column) for column in (0, 2, 3, 4)),
            )
            self.assertEqual(table.horizontalScrollBar().maximum(), 0)
            self.assertTrue(self.window.scan_button.isVisible())
        self.assertGreater(widths[1], widths[0])

    def test_detail_navigation_uses_the_selected_dictionary_and_main_thread(self):
        incidents = [
            {"incident_classification": "First incident", "summary_text": "First summary"},
            {"incident_classification": "Second incident", "summary_text": "Second summary"},
        ]
        self.window.show()
        self.window._show_scan_results(incidents)
        table = self.window.incident_table
        self.assertFalse(self.window.detail_button.isEnabled())
        table.setCurrentCell(1, 0)
        table.setFocus()
        self.app.processEvents()
        self.assertTrue(self.window.detail_button.isEnabled())
        QTest.keyClick(table, Qt.Key.Key_Return)
        detail = self.window.incident_detail
        self.assertIs(self.window.page_stack.currentWidget(), detail)
        self.assertIs(detail.incident, incidents[1])
        self.assertEqual(detail.summary.text(), "Second summary")

        detail.back_button.click()
        self.assertIs(self.window.page_stack.currentWidget(), self.window.dashboard)
        self.assertIs(self.window.results_stack.currentWidget(), table)
        self.assertEqual(table.currentRow(), 1)
        target = table.visualItemRect(table.item(0, 1)).center()
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=target)
        QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=target)
        self.assertIs(self.window.page_stack.currentWidget(), detail)
        self.assertIs(detail.incident, incidents[0])
        self.assertEqual(detail.summary.text(), "First summary")
        self.assertTrue(all(thread == self.app.thread() for _, thread in self.window.slot_threads))

    def test_detail_mapping_survives_row_reordering(self):
        incidents = [
            {"incident_classification": "Alpha", "summary_text": "Alpha summary"},
            {"incident_classification": "Zulu", "summary_text": "Zulu summary"},
        ]
        self.window._show_scan_results(incidents)
        table = self.window.incident_table
        table.sortItems(1, Qt.SortOrder.DescendingOrder)
        table.selectRow(0)
        self.window.detail_button.click()
        self.assertIs(self.window.incident_detail.incident, incidents[1])
        self.assertEqual(self.window.incident_detail.summary.text(), "Zulu summary")

    def test_new_scan_clears_previous_details_even_when_it_fails(self):
        self.window._show_scan_results([{
            "incident_classification": "Old incident",
            "llm_analysis": {"explanation": "Old AI response"},
            "events": [{"message": "Old source message"}],
        }])
        self.window.incident_table.selectRow(0)
        self.window.detail_button.click()
        with patch("ui.main_window.scan_system", side_effect=RuntimeError("scan failed")):
            self.window.scan_button.click()
            self.assertIsNone(self.window.incident_detail.incident)
            self.assertEqual(self.window._incidents, [])
            self.assertFalse(self.window.detail_button.isEnabled())
            self.assertIs(self.window.results_stack.currentWidget(), self.window.empty_state)
            self.finish_scan(self.window._scan_worker, self.window._scan_thread)
        self.assertIs(self.window.page_stack.currentWidget(), self.window.dashboard)
        self.assertNotIn("Old source", self.window.incident_detail.sources.message.toPlainText())
        self.assertEqual(self.window.incident_table.rowCount(), 0)
        self.assertFalse(self.window.detail_button.isEnabled())

    def test_back_shortcuts_preserve_dashboard_selection_and_scroll_without_ai_requests(self):
        self.window.show()
        incidents = [{"incident_classification": f"Incident {i}"} for i in range(40)]
        self.window._show_scan_results(incidents)
        table = self.window.incident_table
        table.setCurrentCell(30, 1)
        table.scrollToItem(table.item(30, 1))
        self.app.processEvents()
        position = table.verticalScrollBar().value()
        with patch("main.analyse_incident_with_llm") as analyse, patch(
            "ui.main_window.scan_system"
        ) as scan:
            for key, modifiers in ((Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
                                   (Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)):
                self.window.detail_button.click()
                detail = self.window.incident_detail
                detail.tabs.setCurrentIndex(2)
                detail.sources.message.setFocus()
                self.app.processEvents()
                QTest.keyClick(detail.sources.message, key, modifiers)
                self.assertIs(self.window.page_stack.currentWidget(), self.window.dashboard)
                self.assertEqual(table.currentRow(), 30)
                self.assertEqual(table.verticalScrollBar().value(), position)
            analyse.assert_not_called()
            scan.assert_not_called()

    def test_repopulation_with_sorting_enabled_keeps_complete_rows_and_source_mapping(self):
        table = self.window.incident_table
        table.setSortingEnabled(True)
        table.sortItems(1, Qt.SortOrder.DescendingOrder)
        incidents = [
            {"incident_classification": "Alpha", "incident_score": 20, "event_count": 1},
            {"incident_classification": "Zulu", "incident_score": 80, "event_count": 7},
            {"incident_classification": "Beta", "incident_score": 40, "event_count": 3},
        ]
        self.window._show_scan_results(incidents)
        for row, source in enumerate((incidents[1], incidents[2], incidents[0])):
            table.setCurrentCell(row, 1)
            self.window.detail_button.click()
            self.assertIs(self.window.incident_detail.incident, source)
            self.assertEqual(table.item(row, 2).text(), f"{source['incident_score']} / 100")
            self.assertEqual(table.item(row, 4).text(), str(source["event_count"]))
            self.window.incident_detail.back_button.click()

    def test_source_reader_fits_minimum_window_and_preserves_long_messages(self):
        message = "  Event <xml> & diagnostic output\n" + "x" * 20000 + "\nTrailing text  "
        self.window.show()
        self.window._show_scan_results([{
            "incident_classification": "DCOM Server Start Timeout",
            "incident_priority": "High", "incident_score": 70,
            "events": [{"event_id": 10010, "provider": "DCOM", "level": "Error",
                        "time_generated": "2026-09-09T20:07:13", "log_type": "System",
                        "message": message}],
        }])
        self.window.incident_table.setCurrentCell(0, 0)
        self.window.detail_button.click()
        detail = self.window.incident_detail
        detail.tabs.setCurrentIndex(2)
        for width, height in ((800, 620), (1280, 900), (800, 620)):
            self.window.resize(width, height)
            self.app.processEvents()
            reader = detail.sources.message
            self.assertEqual(reader.toPlainText(), message)
            self.assertGreater(reader.verticalScrollBar().maximum(), 0)
            self.assertEqual(reader.horizontalScrollBar().maximum(), 0)
            bottom = reader.mapTo(detail.sources, QPoint(0, reader.height())).y()
            self.assertLessEqual(bottom, detail.sources.height())
            self.assertLessEqual(detail.sources.height(), detail.tabs.height())

    def test_diagnostic_commands_copy_as_literal_text_without_execution_and_allow_back_navigation(self):
        command = "Get-Service -Name '<literal> & name'\n# Recommended command only"
        self.window.show()
        with patch("subprocess.Popen") as popen, patch("os.system") as system, patch(
            "ui.main_window.scan_system"
        ) as scan, patch("main.analyse_incident_with_llm") as analyse:
            self.window._show_scan_results([{
                "incident_classification": "Service incident",
                "llm_analysis": {"diagnostic_steps": [
                    {"description": "Inspect the affected service.", "command": command,
                     "risk_level": "safe"},
                ]},
            }])
            self.window.incident_table.setCurrentCell(0, 0)
            self.window.detail_button.click()
            detail = self.window.incident_detail
            detail.tabs.setCurrentIndex(1)
            card = detail.diagnostic_cards[0]
            detail.ai_scroll.ensureWidgetVisible(card.command_box)
            card.command_box.setFocus()
            self.app.processEvents()
            QTest.mouseClick(card.command_box.viewport(), Qt.MouseButton.LeftButton)
            QTest.keyClick(card.command_box, Qt.Key.Key_Return)
            QTest.keyClick(card.command_box, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(card.command_box, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(self.app.clipboard().text(), command)
            self.app.clipboard().clear()
            card.copy_button.click()
            self.assertEqual(self.app.clipboard().text(), command)
            self.assertEqual(card.command_box.toPlainText(), command)
            QTest.keyClick(card.copy_button, Qt.Key.Key_Escape)
            self.assertIs(self.window.page_stack.currentWidget(), self.window.dashboard)
            self.assertEqual(self.window.incident_table.currentRow(), 0)
            popen.assert_not_called()
            system.assert_not_called()
            scan.assert_not_called()
            analyse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
