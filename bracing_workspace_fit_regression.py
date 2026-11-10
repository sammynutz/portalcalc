"""Small-screen workspace resizing, footer reachability and spacing controls."""
import os
import tempfile
import unittest
from unittest import mock


class WorkspaceFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(QT_QPA_PLATFORM='offscreen',PORTALCALC_LICENSE_ENFORCED='0')
        cls.settings=tempfile.TemporaryDirectory();os.environ['APPDATA']=cls.settings.name
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont,QFontDatabase
        from main import MainWindow
        from bracing_analysis_regression import fixed_layout
        from bracing_analysis_desktop import prepare_context
        cls.app=QApplication.instance() or QApplication([])
        cls.original_font=cls.app.font()
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf');cls.app.setFont(QFont('Arial',11))
        cls.w=MainWindow();cls.w.confirm_on_close=False
        cls.w.bracing_location_layout=fixed_layout(cls.w.current_bracing_layout_geometry(),both_ends=True).to_dict()
        cls.w.generate_and_solve();cls.context=prepare_context(cls.w)

    @classmethod
    def tearDownClass(cls):
        cls.w.close();cls.app.setFont(cls.original_font);cls.settings.cleanup()

    def dialog(self, saved=None, read_only=False):
        from PySide6.QtCore import QRect
        from bracing_workspace_ui import BracingAnalysisDialog
        with mock.patch('bracing_workspace_ui.workspace_available_geometry',return_value=QRect(0,0,1024,600)):
            return BracingAnalysisDialog(self.context,self.w.catalog,saved or {'mixed_struts':False},read_only=read_only)

    def test_small_screen_can_shrink_with_long_results_and_footer_stays_clickable(self):
        from PySide6.QtCore import QPoint,QRect,Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QDialog
        d=self.dialog()
        try:
            d.show();self.app.processEvents()
            self.assertLessEqual(d.frameGeometry().height(),600)
            self.assertLessEqual(d.frameGeometry().width(),1024)
            d.result_summary_label.setText('\n'.join(['A long result summary with wind cases, member checks and layout spacing.']*30))
            d.result_summary_box.show();d.resize(800,420);self.app.processEvents()
            self.assertEqual(d.height(),420)
            self.assertLessEqual(d.minimumSizeHint().height(),420)
            self.assertGreater(d.content_scroll.verticalScrollBar().maximum(),0)
            for value in (0,d.content_scroll.verticalScrollBar().maximum()):
                d.content_scroll.verticalScrollBar().setValue(value);self.app.processEvents()
                for button in (d.save_button,d.cancel_button,d.export_text_button,d.export_json_button):
                    self.assertTrue(d.rect().contains(QRect(button.mapTo(d,QPoint()),button.size())))
                    self.assertFalse(d.content_scroll.isAncestorOf(button))
            d.resize(960,530);self.app.processEvents();self.assertEqual(d.height(),530)
            QTest.mouseClick(d.cancel_button,Qt.LeftButton)
            self.assertFalse(d.isVisible())
        finally:d.reject()

    def test_spacing_default_persists_invalidates_and_is_locked_in_review(self):
        d=self.dialog()
        try:
            self.assertEqual(d.max_braced_bay_spacing.value(),5)
            d.max_braced_bay_spacing.setValue(3)
            saved=d.snapshot()
            self.assertEqual(saved['layout_search']['max_braced_bay_spacing'],3)
            self.assertEqual(d.current_context()['max_braced_bay_spacing'],3)
            restored=self.dialog(saved,read_only=True)
            try:
                self.assertEqual(restored.max_braced_bay_spacing.value(),3)
                self.assertFalse(restored.max_braced_bay_spacing.isEnabled())
            finally:restored.reject()
            d.result=object()
            d.max_braced_bay_spacing.setValue(4)
            self.assertIsNone(d.result)
            self.assertFalse(d.export_json_button.isEnabled())
        finally:d.reject()


if __name__=='__main__':unittest.main(verbosity=2)
