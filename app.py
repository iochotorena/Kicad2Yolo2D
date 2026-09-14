#!/usr/bin/env python3
"""PySide6 desktop application for KiCad extraction and validation."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from kicad_extract import default_output_directory, process_source, save_processing_result


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
        self.resize(1200, 800)

        self.results = []
        self.active_output_dir: Path | None = None
        self.auto_output_path: str | None = None
        self.worker_thread: QThread | None = None
        self.worker: ProcessingWorker | None = None

        self._build_ui()
        self._build_menu()
        self.setStatusBar(QStatusBar(self))

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        form_layout = QFormLayout()

        self.source_edit = QLineEdit()
        source_button = QPushButton("Examinar…")
        source_button.clicked.connect(self.choose_source)
        form_layout.addRow("Origen:", self._row_widget(self.source_edit, source_button))

        self.output_edit = QLineEdit()
        output_button = QPushButton("Examinar…")
        output_button.clicked.connect(self.choose_output_directory)
        form_layout.addRow("Destino:", self._row_widget(self.output_edit, output_button))

        layout.addLayout(form_layout)

        buttons_layout = QHBoxLayout()
        process_button = QPushButton("Procesar")
        process_button.clicked.connect(self.process_current_source)
        save_button = QPushButton("Guardar resultados")
        save_button.clicked.connect(self.save_results)
        buttons_layout.addWidget(process_button)
        buttons_layout.addWidget(save_button)
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

        self.components_table = QTableWidget(0, 10)
        self.components_table.setHorizontalHeaderLabels(
            [
                "Name",
                "Reference",
                "Value",
                "Center X",
                "Center Y",
                "BBox X",
                "BBox Y",
                "Width",
                "Height",
                "Fuente",
            ]
        )
        tabs.addTab(self.components_table, "Componentes")

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        tabs.addTab(self.log_edit, "Registro")

        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Archivo")

        open_file_action = QAction("Abrir fichero .kicad_pcb", self)
        open_file_action.triggered.connect(self.open_file_from_menu)
        file_menu.addAction(open_file_action)

        open_folder_action = QAction("Abrir carpeta", self)
        open_folder_action.triggered.connect(self.open_folder_from_menu)
        file_menu.addAction(open_folder_action)

        select_output_action = QAction("Seleccionar carpeta de salida", self)
        select_output_action.triggered.connect(self.choose_output_directory)
        file_menu.addAction(select_output_action)

        file_menu.addSeparator()

        save_action = QAction("Guardar resultados", self)
        save_action.triggered.connect(self.save_results)
        file_menu.addAction(save_action)

        save_as_action = QAction("Guardar como…", self)
        save_as_action.triggered.connect(self.save_results_as)
        file_menu.addAction(save_as_action)

        open_results_action = QAction("Abrir carpeta de resultados", self)
        open_results_action.triggered.connect(self.open_results_directory)
        file_menu.addAction(open_results_action)

        file_menu.addSeparator()

        exit_action = QAction("Salir", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

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
        path = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de salida", self.output_edit.text() or str(Path.cwd()))
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
        self.active_output_dir = Path(self.output_edit.text().strip())
        self._populate_results()
        self._append_log(f"Procesamiento completado para {len(self.results)} fichero(s).")
        self.statusBar().showMessage("Procesamiento completado", 5000)
        self._set_processing_state(False)

    @Slot(str)
    def _processing_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Error de procesamiento", message)
        self._append_log(f"Error: {message}")
        self._set_processing_state(False)

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
                str(result.output_dir or ""),
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

    def show_selected_result_details(self) -> None:
        row = self.results_table.currentRow()
        if row < 0 or row >= len(self.results):
            return

        result = self.results[row]
        self.components_table.setRowCount(0)
        for component in result.components:
            current_row = self.components_table.rowCount()
            self.components_table.insertRow(current_row)
            values = [
                component["name"],
                component["reference"],
                component.get("value", ""),
                f"{component['center_x']:.4f}",
                f"{component['center_y']:.4f}",
                f"{component['bbox_center_x']:.4f}",
                f"{component['bbox_center_y']:.4f}",
                f"{component['width']:.4f}",
                f"{component['height']:.4f}",
                component.get("bbox_source", ""),
            ]
            for column, value in enumerate(values):
                self.components_table.setItem(current_row, column, QTableWidgetItem(value))

        self.log_edit.setPlainText(
            "\n".join(
                [
                    f"PCB: {result.pcb_path}",
                    f"Salida: {result.output_dir}",
                    f"Footprints: {result.stats.total_footprints}",
                    f"Exportados: {result.stats.exported_components}",
                    f"Courtyard: {result.stats.components_with_courtyard}",
                    f"Reparados desde pads: {result.stats.components_repaired_from_pads}",
                    f"Omitidos: {result.stats.skipped_components}",
                    f"PCB bbox: ({result.pcb_dimensions.min_x:.2f}, {result.pcb_dimensions.min_y:.2f}) -> "
                    f"({result.pcb_dimensions.max_x:.2f}, {result.pcb_dimensions.max_y:.2f})",
                    "",
                    "Avisos:",
                    *(result.warnings or ["Sin avisos."]),
                ]
            )
        )

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
                    save_processing_result(result, directory / result.pcb_path.stem)
            self.active_output_dir = directory
            self._populate_results()
            self._append_log(f"Resultados guardados en {directory}")
            self.statusBar().showMessage(f"Resultados guardados en {directory}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Error al guardar", str(exc))
            self._append_log(f"Error al guardar: {exc}")

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
        if active:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self.statusBar().showMessage("Procesando…")
        else:
            QApplication.restoreOverrideCursor()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
