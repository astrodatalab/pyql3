from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, 
    QHBoxLayout, QMessageBox, QLineEdit, QComboBox, QLabel
)
from PySide6.QtCore import Qt

class HeaderEditorDialog(QDialog):
    def __init__(self, fits_reader, parent=None):
        super().__init__(parent)
        self.fits_reader = fits_reader
        self.current_ext = getattr(fits_reader, 'current_ext', 0)
        self.setWindowTitle("FITS Header Editor")
        self.resize(550, 450)
        
        self.layout = QVBoxLayout(self)
        
        # Extension Selector Layout
        ext_layout = QHBoxLayout()
        ext_layout.addWidget(QLabel("Extension:"))
        self.combo_ext = QComboBox()
        self.populate_extensions()
        self.combo_ext.currentIndexChanged.connect(self.on_extension_changed)
        ext_layout.addWidget(self.combo_ext, stretch=1)
        self.layout.addLayout(ext_layout)
        
        # Search bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search headers (Keyword, Value, or Comment)...")
        self.search_bar.textChanged.connect(self.filter_table)
        self.layout.addWidget(self.search_bar)
        
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Keyword", "Value", "Comment"])
        self.layout.addWidget(self.table)
        
        self.button_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save Header")
        self.save_btn.clicked.connect(self.save_header)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        
        self.button_layout.addWidget(self.save_btn)
        self.button_layout.addWidget(self.cancel_btn)
        self.layout.addLayout(self.button_layout)
        
        self.populate_table(self.current_ext)

    def populate_extensions(self):
        self.combo_ext.blockSignals(True)
        self.combo_ext.clear()
        extensions = self.fits_reader.get_all_extensions()
        for idx, name in extensions:
            self.combo_ext.addItem(f"{idx}: {name}", userData=idx)
            
        cur_idx = self.combo_ext.findData(self.current_ext)
        if cur_idx >= 0:
            self.combo_ext.setCurrentIndex(cur_idx)
        self.combo_ext.blockSignals(False)

    def on_extension_changed(self, index):
        # Save edits from table to previous extension header in memory before switching
        self.apply_table_edits(ext=self.current_ext)
        new_ext = self.combo_ext.currentData()
        if new_ext is not None:
            self.current_ext = new_ext
            self.search_bar.clear()
            self.populate_table(self.current_ext)

    def populate_table(self, ext=None):
        header = self.fits_reader.get_header(ext=ext)
        if header is None:
            self.table.setRowCount(0)
            return
            
        self.table.setRowCount(len(header))
        row = 0
        for keyword, value in header.items():
            try:
                comment = header.comments[keyword]
            except (KeyError, IndexError):
                comment = ""
            
            # Create uneditable item for keyword
            kw_item = QTableWidgetItem(str(keyword))
            kw_item.setFlags(kw_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  
            
            self.table.setItem(row, 0, kw_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(value)))
            self.table.setItem(row, 2, QTableWidgetItem(str(comment)))
            row += 1

    def filter_table(self, text):
        text = text.lower()
        for row in range(self.table.rowCount()):
            match = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and text in item.text().lower():
                    match = True
                    break
            self.table.setRowHidden(row, not match)

    def apply_table_edits(self, ext=None):
        header = self.fits_reader.get_header(ext=ext)
        if header is None:
            return
            
        for row in range(self.table.rowCount()):
            keyword_item = self.table.item(row, 0)
            value_item = self.table.item(row, 1)
            comment_item = self.table.item(row, 2)
            
            if keyword_item and value_item:
                keyword = keyword_item.text()
                value = value_item.text()
                
                # Basic type conversion attempt
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        if value.lower() in ('true', 't'):
                            value = True
                        elif value.lower() in ('false', 'f'):
                            value = False
                            
                comment = comment_item.text() if comment_item else ""
                
                # Skip special keywords
                if keyword in ('', 'COMMENT', 'HISTORY'):
                    continue
                    
                self.fits_reader.update_header_card(keyword, value, comment, ext=ext)

    def save_header(self):
        try:
            # Apply current table edits to currently selected extension
            self.apply_table_edits(ext=self.current_ext)
            
            if not self.fits_reader.filepath:
                # In-memory data: prompt user via main window save_file_as if available
                if self.parent() and hasattr(self.parent(), 'save_file_as'):
                    self.parent().save_file_as()
                else:
                    QMessageBox.information(self, "Header Updated", "Header cards updated in memory.")
            else:
                reply = QMessageBox.question(
                    self, 
                    "Confirm Overwrite", 
                    f"Save header changes directly to file?\n\n{self.fits_reader.filepath}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.fits_reader.save()
                    QMessageBox.information(self, "Success", "FITS Header saved successfully.")
                else:
                    QMessageBox.information(self, "Header Updated", "Header cards updated in memory (file not overwritten).")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save header: {str(e)}")
