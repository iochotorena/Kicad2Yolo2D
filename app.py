#!/usr/bin/env python3
"""PySide6 desktop application for KiCad extraction and validation."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from scr.kicad_extract import build_output_directory, default_output_directory, process_source, save_processing_result
from scr.pcb_export import RasterExportDialog, VectorExportDialog, export_scene_to_raster, export_scene_to_svg
from scr.pcb_viewer_model import BoardModel, build_board_model
from scr.pcb_viewer_widget import PCBViewerWidget


class ProcessingWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Path, output: Path) -> None:
        super().__init__()
        self.source = source
        self.output = output

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(process_source(self.source, self.output))
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KiCad2Yolo2D")
        self.resize(1480, 920)

        self.results = []
        self.active_output_dir: Path | None = None
        self.auto_output_path: str | None = None
        self.processing_cursor_active = False
        self.worker_thread: QThread | None = None
        self.worker: ProcessingWorker | None = None
        self.processing_controls: list[QWidget] = []
        self.processing_actions: list[QAction] = []
        self.result_models: dict[int, BoardModel] = {}
        self.current_result_row = -1
        self._syncing_component_selection = False

        self._build_ui()
        self._build_menu()
        self.setStatusBar(QStatusBar(self))

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        form_layout = QFormLayout()

        self.source_edit = QLineEdit()
        self.source_button = QPushButton("Examinar…")
        self.source_button.clicked.connect(self.choose_source)
        form_layout.addRow("Origen:", self._row_widget(self.source_edit, self.source_button))

        self.output_edit = QLineEdit()
        self.output_button = QPushButton("Examinar…")
        self.output_button.clicked.connect(self.choose_output_directory)
        form_layout.addRow("Destino:", self._row_widget(self.output_edit, self.output_button))
        layout.addLayout(form_layout)

        buttons_layout = QHBoxLayout()
        self.process_button = QPushButton("Procesar")
        self.process_button.clicked.connect(self.process_current_source)
        self.save_button = QPushButton("Guardar resultados")
        self.save_button.clicked.connect(self.save_results)
        buttons_layout.addWidget(self.process_button)
        buttons_layout.addWidget(self.save_button)
        buttons_layout.addStretch(1)
        layout.addLayout(buttons_layout)

        self.summary_label = QLabel("Selecciona un fichero .kicad_pcb o una carpeta para comenzar.")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.results_table = QTableWidget(0, 6)
        self.results_table.setHorizontalHeaderLabels(
            ["PCB", "Componentes", "Courtyard", "Reparados", "Avisos", "Salida"]
        )
        self.results_table.itemSelectionChanged.connect(self.show_selected_result_details)
        splitter.addWidget(self.results_table)

        tabs = QTabWidget()

        self.components_table = QTableWidget(0, 12)
        self.components_table.setHorizontalHeaderLabels(
            [
                "Name",
                "Reference",
                "Value",
                "Side",
                "X (mm)",
                "Y (mm)",
                "Rotation",
                "Width",
                "Height",
                "Fuente",
                "Estado",
                "Avisos",
            ]
        )
        self.components_table.itemSelectionChanged.connect(self._component_table_selection_changed)
        tabs.addTab(self.components_table, "Componentes")

        self.viewer_widget = PCBViewerWidget(self)
        self.viewer_widget.footprintSelected.connect(self._viewer_selected_footprint)
        self.viewer_widget.visibleReferencesChanged.connect(self._apply_component_table_visibility)
        tabs.addTab(self.viewer_widget, "PCB Viewer")

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        tabs.addTab(self.log_edit, "Registro")

        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

        self.processing_controls = [
            self.source_edit,
            self.source_button,
            self.output_edit,
            self.output_button,
            self.process_button,
            self.save_button,
        ]
        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Archivo")

        self.open_file_action = QAction("Abrir fichero .kicad_pcb", self)
        self.open_file_action.triggered.connect(self.open_file_from_menu)
        file_menu.addAction(self.open_file_action)

        self.open_folder_action = QAction("Abrir carpeta", self)
        self.open_folder_action.triggered.connect(self.open_folder_from_menu)
        file_menu.addAction(self.open_folder_action)

        self.select_output_action = QAction("Seleccionar carpeta de salida", self)
        self.select_output_action.triggered.connect(self.choose_output_directory)
        file_menu.addAction(self.select_output_action)

        file_menu.addSeparator()

        export_menu = file_menu.addMenu("Export")
        self.export_raster_action = QAction("Raster image", self)
        self.export_raster_action.triggered.connect(self.export_raster_image)
        export_menu.addAction(self.export_raster_action)
        self.export_vector_action = QAction("Vector image", self)
        self.export_vector_action.triggered.connect(self.export_vector_image)
        export_menu.addAction(self.export_vector_action)

        file_menu.addSeparator()

        self.save_action = QAction("Guardar resultados", self)
        self.save_action.triggered.connect(self.save_results)
        file_menu.addAction(self.save_action)

        self.save_as_action = QAction("Guardar como…", self)
        self.save_as_action.triggered.connect(self.save_results_as)
        file_menu.addAction(self.save_as_action)

        open_results_action = QAction("Abrir carpeta de resultados", self)
        open_results_action.triggered.connect(self.open_results_directory)
        file_menu.addAction(open_results_action)

        file_menu.addSeparator()

        exit_action = QAction("Salir", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        self.processing_actions = [
            self.open_file_action,
            self.open_folder_action,
            self.select_output_action,
            self.save_action,
            self.save_as_action,
            self.export_raster_action,
            self.export_vector_action,
        ]

    @staticmethod
    def _row_widget(line_edit: QLineEdit, button: QPushButton) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit)
        layout.addWidget(button)
        return widget

    def open_file_from_menu(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir fichero KiCad PCB",
            str(Path.cwd()),
            "KiCad PCB (*.kicad_pcb)",
        )
        if path:
            self.set_source_path(path)

    def open_folder_from_menu(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Abrir carpeta", str(Path.cwd()))
        if path:
            self.set_source_path(path)

    def choose_source(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar fichero KiCad PCB",
            str(Path.cwd()),
            "KiCad PCB (*.kicad_pcb)",
        )
        if file_path:
            self.set_source_path(file_path)
            return
        directory_path = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", str(Path.cwd()))
        if directory_path:
            self.set_source_path(directory_path)

    def choose_output_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta de salida",
            self.output_edit.text() or str(Path.cwd()),
        )
        if path:
            self.output_edit.setText(path)
            self.auto_output_path = None

    def set_source_path(self, path: str) -> None:
        source = Path(path)
        self.source_edit.setText(str(source))
        default_output = str(default_output_directory(source))
        current_output = self.output_edit.text().strip()
        if not current_output or current_output == self.auto_output_path:
            self.output_edit.setText(default_output)
            self.auto_output_path = default_output

    def process_current_source(self) -> None:
        if self.worker_thread is not None and self.worker_thread.isRunning():
            self.statusBar().showMessage("Ya hay un procesamiento en curso.", 5000)
            return

        source_text = self.source_edit.text().strip()
        if not source_text:
            QMessageBox.warning(self, "Origen requerido", "Selecciona un fichero .kicad_pcb o una carpeta.")
            return

        source = Path(source_text)
        if not source.exists():
            QMessageBox.warning(self, "Origen inválido", "La ruta indicada no existe.")
            return

        output_text = self.output_edit.text().strip() or str(default_output_directory(source))
        self.output_edit.setText(output_text)
        if output_text == str(default_output_directory(source)):
            self.auto_output_path = output_text

        self._set_processing_state(True)
        self.worker_thread = QThread(self)
        self.worker = ProcessingWorker(source, Path(output_text))
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._processing_finished)
        self.worker.failed.connect(self._processing_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.start()

    @Slot(object)
    def _processing_finished(self, results: object) -> None:
        self.results = list(results)
        self.result_models.clear()
        self.current_result_row = -1
        self.active_output_dir = Path(self.output_edit.text().strip())
        self._populate_results()
        self._set_processing_state(False)
        self._append_log(
            f"Procesamiento completado para {len(self.results)} fichero(s). Usa Guardar resultados para persistirlos."
        )
        self.statusBar().showMessage("Procesamiento completado", 5000)

    @Slot(str)
    def _processing_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Error de procesamiento", message)
        self._set_processing_state(False)
        self._append_log(f"Error: {message}")

    @Slot()
    def _cleanup_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
            self.worker_thread = None

    def _populate_results(self) -> None:
        self.results_table.setRowCount(0)
        total_components = 0
        total_repaired = 0
        total_warnings = 0
        for result in self.results:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            total_components += result.stats.exported_components
            total_repaired += result.stats.components_repaired_from_pads
            total_warnings += len(result.warnings)
            values = [
                result.pcb_path.name,
                str(result.stats.exported_components),
                str(result.stats.components_with_courtyard),
                str(result.stats.components_repaired_from_pads),
                str(len(result.warnings)),
                str(result.output_dir or result.planned_output_dir or ""),
            ]
            for column, value in enumerate(values):
                self.results_table.setItem(row, column, QTableWidgetItem(value))
        self.summary_label.setText(
            f"PCB procesadas: {len(self.results)} | "
            f"Componentes exportados: {total_components} | "
            f"Bounding boxes reparadas: {total_repaired} | "
            f"Avisos: {total_warnings}"
        )
        if self.results:
            self.results_table.selectRow(0)

    def _current_model(self) -> BoardModel | None:
        row = self.results_table.currentRow()
        if row < 0 or row >= len(self.results):
            return None
        if row not in self.result_models:
            self.result_models[row] = build_board_model(self.results[row])
        return self.result_models[row]

    def show_selected_result_details(self) -> None:
        row = self.results_table.currentRow()
        if row < 0 or row >= len(self.results):
            return
        self.current_result_row = row
        result = self.results[row]
        model = self._current_model()
        if model:
            self.viewer_widget.load_model(model)
            self._populate_components(model)
        warning_lines = model.warnings if model else result.warnings
        self.log_edit.setPlainText(
            "\n".join(
                [
                    f"PCB: {result.pcb_path}",
                    f"Salida: {result.output_dir or result.planned_output_dir}",
                    f"Footprints: {result.stats.total_footprints}",
                    f"Exportados: {result.stats.exported_components}",
                    f"Courtyard: {result.stats.components_with_courtyard}",
                    f"Reparados desde pads: {result.stats.components_repaired_from_pads}",
                    f"Omitidos: {result.stats.skipped_components}",
                    f"PCB bbox: ({result.pcb_dimensions.min_x:.2f}, {result.pcb_dimensions.min_y:.2f}) -> "
                    f"({result.pcb_dimensions.max_x:.2f}, {result.pcb_dimensions.max_y:.2f})",
                    "",
                    "Avisos:",
                    *(warning_lines or ["Sin avisos."]),
                ]
            )
        )

    def _populate_components(self, model: BoardModel) -> None:
        self._syncing_component_selection = True
        self.components_table.setRowCount(0)
        for footprint in model.footprints:
            row = self.components_table.rowCount()
            self.components_table.insertRow(row)
            bbox = footprint.bbox
            warning_text = "; ".join(footprint.warnings_short) if footprint.warnings_short else "-"
            values = [
                footprint.name,
                footprint.reference,
                footprint.value,
                footprint.side,
                f"{footprint.position[0]:.3f}",
                f"{footprint.position[1]:.3f}",
                f"{footprint.rotation:.2f}°",
                f"{bbox.width:.3f}" if bbox else "-",
                f"{bbox.height:.3f}" if bbox else "-",
                bbox.source_label if bbox else "missing",
                footprint.row_status,
                warning_text,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setData(Qt.ItemDataRole.UserRole, footprint.reference)
                self.components_table.setItem(row, column, item)
            row_color = self._footprint_row_color(footprint)
            for column in range(self.components_table.columnCount()):
                self.components_table.item(row, column).setBackground(row_color)
        self._syncing_component_selection = False
        self._apply_component_table_visibility(sorted(self.viewer_widget.visible_references))

    @staticmethod
    def _footprint_row_color(footprint) -> QColor:
        if footprint.bbox is None:
            return QColor("#fee2e2")
        if footprint.warnings_short:
            return QColor("#fef3c7")
        if footprint.bbox.source == "pads":
            return QColor("#ffedd5")
        return QColor("#ecfdf5")

    def _component_table_selection_changed(self) -> None:
        if self._syncing_component_selection:
            return
        row = self.components_table.currentRow()
        if row < 0:
            return
        item = self.components_table.item(row, 1)
        if item is None:
            return
        reference = item.data(Qt.ItemDataRole.UserRole) or item.text()
        if isinstance(reference, str) and reference:
            self.viewer_widget.select_reference(reference, center=True)

    def _viewer_selected_footprint(self, reference: str) -> None:
        self._syncing_component_selection = True
        for row in range(self.components_table.rowCount()):
            item = self.components_table.item(row, 1)
            if item and (item.data(Qt.ItemDataRole.UserRole) or item.text()) == reference:
                self.components_table.selectRow(row)
                self.components_table.scrollToItem(item)
                break
        self._syncing_component_selection = False

    def _apply_component_table_visibility(self, visible_references: list[str]) -> None:
        visible_set = set(visible_references)
        filters_active = self.viewer_widget.has_active_filters()
        if not visible_set and self.viewer_widget.model is not None and not filters_active:
            visible_set = {
                footprint.reference
                for footprint in self.viewer_widget.model.footprints
                if footprint.reference
            }
        for row in range(self.components_table.rowCount()):
            item = self.components_table.item(row, 1)
            reference = item.data(Qt.ItemDataRole.UserRole) if item else None
            hide = filters_active or bool(visible_set)
            if isinstance(reference, str) and reference:
                hide = hide and reference not in visible_set
            else:
                hide = filters_active
            self.components_table.setRowHidden(row, hide)

    def save_results(self) -> None:
        if not self.results:
            QMessageBox.information(self, "Sin resultados", "Procesa primero un fichero o carpeta.")
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            self.save_results_as()
            return
        self._save_to_directory(Path(output_text))

    def save_results_as(self) -> None:
        if not self.results:
            QMessageBox.information(self, "Sin resultados", "Procesa primero un fichero o carpeta.")
            return
        path = QFileDialog.getExistingDirectory(self, "Guardar como", self.output_edit.text() or str(Path.cwd()))
        if not path:
            return
        self.output_edit.setText(path)
        self.auto_output_path = None
        self._save_to_directory(Path(path))

    def _save_to_directory(self, directory: Path) -> None:
        source = Path(self.source_edit.text().strip())
        try:
            if source.is_file():
                save_processing_result(self.results[0], directory)
            else:
                for result in self.results:
                    save_processing_result(result, build_output_directory(source, directory, result.pcb_path))
            self.active_output_dir = directory
            self._populate_results()
            self._append_log(f"Resultados guardados en {directory}")
            self.statusBar().showMessage(f"Resultados guardados en {directory}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Error al guardar", str(exc))
            self._append_log(f"Error al guardar: {exc}")

    def export_raster_image(self) -> None:
        model = self._current_model()
        if not model:
            QMessageBox.information(self, "Sin PCB", "Procesa y selecciona una PCB primero.")
            return
        destination, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export raster image",
            str(model.pcb_path.with_suffix(".png")),
            "PNG (*.png);;JPEG (*.jpg *.jpeg)",
        )
        if not destination:
            return
        dialog = RasterExportDialog(model, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        options = dialog.options()
        state = self.viewer_widget.capture_visibility_state()
        try:
            self.viewer_widget.apply_export_tokens(options.include_tokens)
            suffix = Path(destination).suffix.lower()
            image_format = "jpg" if suffix in {".jpg", ".jpeg"} or "jpeg" in selected_filter.lower() else "png"
            export_scene_to_raster(
                self.viewer_widget.scene,
                self.viewer_widget.board_rect(),
                destination,
                options.width_px,
                options.height_px,
                image_format,
            )
        finally:
            self.viewer_widget.restore_visibility_state(state)
        self.statusBar().showMessage(f"Raster exportado en {destination}", 5000)
        self._append_log(f"Raster exportado en {destination}")

    def export_vector_image(self) -> None:
        model = self._current_model()
        if not model:
            QMessageBox.information(self, "Sin PCB", "Procesa y selecciona una PCB primero.")
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export vector image",
            str(model.pcb_path.with_suffix(".svg")),
            "SVG (*.svg)",
        )
        if not destination:
            return
        dialog = VectorExportDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        options = dialog.options()
        state = self.viewer_widget.capture_visibility_state()
        try:
            self.viewer_widget.apply_export_tokens(options.include_tokens)
            export_scene_to_svg(self.viewer_widget.scene, self.viewer_widget.board_rect(), destination, model)
        finally:
            self.viewer_widget.restore_visibility_state(state)
        self.statusBar().showMessage(f"SVG exportado en {destination}", 5000)
        self._append_log(f"SVG exportado en {destination}")

    def open_results_directory(self) -> None:
        target = self.output_edit.text().strip() or (str(self.active_output_dir) if self.active_output_dir else "")
        if not target:
            QMessageBox.information(self, "Sin carpeta", "No hay carpeta de resultados seleccionada.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _append_log(self, message: str) -> None:
        current = self.log_edit.toPlainText().strip()
        self.log_edit.setPlainText(f"{current}\n{message}".strip())

    def _set_processing_state(self, active: bool) -> None:
        for control in self.processing_controls:
            control.setEnabled(not active)
        for action in self.processing_actions:
            action.setEnabled(not active)
        if active:
            if not self.processing_cursor_active:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                self.processing_cursor_active = True
            self.statusBar().showMessage("Procesando…")
        elif self.processing_cursor_active:
            QApplication.restoreOverrideCursor()
            self.processing_cursor_active = False


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
