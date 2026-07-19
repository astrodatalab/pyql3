from PySide6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, QHBoxLayout, QMessageBox, QLineEdit
from PySide6.QtCore import Qt

class HeaderEditorDialog(QDialog):
    def __init__(self, fits_reader, parent=None):
        super().__init__(parent)
        self.fits_reader = fits_reader
        self.setWindowTitle("FITS Header Editor")
        self.resize(500, 400)
        
        self.layout = QVBoxLayout(self)
        
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
        
        self.populate_table()

    def populate_table(self):
        header = self.fits_reader.get_header()
        if header is None:
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

    def save_header(self):
        header = self.fits_reader.get_header()
        if header is None:
            self.accept()
            return
            
        try:
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
                    
                    # Skip special keywords or handle them carefully
                    if keyword in ('', 'COMMENT', 'HISTORY'):
                        continue
                        
                    self.fits_reader.update_header_card(keyword, value, comment)
            
            self.fits_reader.save()
            QMessageBox.information(self, "Success", "FITS Header saved successfully.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save header: {str(e)}")
