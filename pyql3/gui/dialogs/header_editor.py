import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, 
    QHBoxLayout, QMessageBox, QLineEdit, QComboBox, QLabel
)
from PySide6.QtCore import Qt
from astropy.io import fits

from pyql3.core.fits_reader import FitsReader

class HeaderEditorDialog(QDialog):
    def __init__(self, fits_reader, parent=None):
        super().__init__(parent)
        self.fits_reader = fits_reader
        # The file and HDUList this editor's table was read from. The reader is shared
        # with the main window, which can load another file while the editor is open;
        # writing the table then would put this file's cards into that one.
        self._opened_on = (fits_reader.filepath, fits_reader.hdul)
        self.current_ext = getattr(fits_reader, 'current_ext', 0)
        # Edits not yet written, per extension: {ext: {card index: (value, comment)}}.
        # They reach the reader only on Save, so Cancel leaves every header as it was.
        self._held_edits = {}
        self._skipped_keywords = []
        self._applied = []
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

    def _refuse_if_file_changed(self):
        """Warn and return True when the reader no longer holds the file this editor
        was opened on."""
        filepath, hdul = self._opened_on
        if self.fits_reader.filepath == filepath and self.fits_reader.hdul is hdul:
            return False
        opened = os.path.basename(filepath) if filepath else "the in-memory image"
        now = (os.path.basename(self.fits_reader.filepath)
               if self.fits_reader.filepath else "an in-memory image")
        QMessageBox.warning(
            self,
            "File changed",
            f"This editor was opened on {opened}, but the window now shows {now}.\n\n"
            "Nothing was written. Close the editor and open it again to edit the "
            "file now shown.",
        )
        return True

    def on_extension_changed(self, index):
        # The table's edits are kept in the dialog, not written to the reader: Cancel
        # must leave every extension's header as it was (R42).
        if self._refuse_if_file_changed():
            return
        self._hold_table_edits()
        new_ext = self.combo_ext.currentData()
        if new_ext is not None:
            self.current_ext = new_ext
            self.search_bar.clear()
            self.populate_table(self.current_ext)

    @staticmethod
    def _value_text(value):
        # An undefined value (`KEY =` with nothing after it) is shown empty: `str()` of
        # astropy's Undefined is not something to put back in the file.
        return "" if value is fits.card.UNDEFINED else str(value)

    def populate_table(self, ext=None):
        """One row per card. Each row keeps its card's index and the text it was loaded
        with, so a row is written back to its own card, and only if its text changed.
        Edits held for `ext` from an earlier visit are shown again."""
        header = self.fits_reader.get_header(ext=ext)
        if header is None:
            self.table.setRowCount(0)
            return

        held = self._held_edits.get(ext, {})
        self.table.setRowCount(len(header))
        for index, card in enumerate(header.cards):
            commentary = card.keyword in FitsReader.COMMENTARY_KEYWORDS
            value_text = self._value_text(card.value)
            comment_text = "" if commentary else str(card.comment)

            # Create uneditable item for keyword
            kw_item = QTableWidgetItem(str(card.keyword))
            kw_item.setFlags(kw_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            kw_item.setData(Qt.ItemDataRole.UserRole, index)
            value_item = QTableWidgetItem(value_text)
            value_item.setData(Qt.ItemDataRole.UserRole, value_text)
            comment_item = QTableWidgetItem(comment_text)
            comment_item.setData(Qt.ItemDataRole.UserRole, comment_text)
            if commentary:
                # COMMENT, HISTORY and blank cards have no comment field.
                comment_item.setFlags(comment_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if index in held:
                value_item.setText(held[index][0])
                comment_item.setText(held[index][1])

            self.table.setItem(index, 0, kw_item)
            self.table.setItem(index, 1, value_item)
            self.table.setItem(index, 2, comment_item)

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

    def _hold_table_edits(self):
        """Record the rows of the table whose text differs from what was loaded, as
        `{card index: (value text, comment text)}` for the extension shown."""
        edits = {}
        for row in range(self.table.rowCount()):
            keyword_item = self.table.item(row, 0)
            value_item = self.table.item(row, 1)
            comment_item = self.table.item(row, 2)
            if not (keyword_item and value_item and comment_item):
                continue
            shown = (value_item.text(), comment_item.text())
            loaded = (value_item.data(Qt.ItemDataRole.UserRole),
                      comment_item.data(Qt.ItemDataRole.UserRole))
            if shown != loaded:
                edits[keyword_item.data(Qt.ItemDataRole.UserRole)] = shown
        if edits:
            self._held_edits[self.current_ext] = edits
        else:
            self._held_edits.pop(self.current_ext, None)

    @staticmethod
    def _parse_value(text):
        # Basic type conversion attempt
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
        if text.lower() in ('true', 't'):
            return True
        if text.lower() in ('false', 'f'):
            return False
        return text

    def apply_table_edits(self, ext=None):
        """Write the edits held in this dialog into the reader's headers: those of `ext`,
        or of every extension when `ext` is None.

        Each edit goes to the card it was made on, by index, and a row whose text was not
        changed is not written at all. Returns False, having written nothing, when the
        reader has moved to another file since the editor opened.
        """
        self._skipped_keywords = []
        if self._refuse_if_file_changed():
            return False
        self._hold_table_edits()

        targets = list(self._held_edits) if ext is None else [ext]
        # Every edit is turned into a value and checked before any is written, so an edit
        # astropy refuses (a non-ASCII value, say) leaves every header as it was, and Cancel
        # still discards everything.
        planned = []
        for target in targets:
            header = self.fits_reader.get_header(ext=target)
            if header is None:
                continue
            for index, (value_text, comment_text) in sorted(
                    self._held_edits.get(target, {}).items()):
                card = header.cards[index]
                # A row whose comment alone was edited keeps the card's value as it is:
                # parsing its text again would turn the string '123' into an integer and
                # an undefined value into ''.
                if value_text == self._value_text(card.value):
                    value = card.value
                else:
                    value = self._parse_value(value_text)
                self._check_card(card.keyword, value, comment_text)
                planned.append((target, index, card.keyword, value, value_text, comment_text))

        refused = {}
        # What each written card was, so a save that fails can put it back (`_unapply`).
        self._applied = []
        for target, index, keyword, value, value_text, comment_text in planned:
            previous = self.fits_reader.get_header(ext=target).cards[index].image
            # Structural keywords are refused by the reader, and only rows the user
            # changed reach here, so each refusal is reported.
            if self.fits_reader.update_header_card_at(index, value, comment_text, ext=target):
                self._applied.append((target, index, previous, (value_text, comment_text)))
            else:
                self._skipped_keywords.append(keyword)
                refused.setdefault(target, {})[index] = (value_text, comment_text)
        for target in targets:
            written = set(self._held_edits.get(target, {})) - set(refused.get(target, {}))
            if refused.get(target):
                self._held_edits[target] = refused[target]
            else:
                self._held_edits.pop(target, None)
            if target == self.current_ext:
                self._mark_table_as_loaded(written)
        return True

    def _unapply(self):
        """Undo the last `apply_table_edits`: put back the cards it wrote, and hold its edits
        in the dialog again, unsaved, so they are neither in the reader's header nor lost."""
        for target, index, previous, texts in reversed(self._applied):
            header = self.fits_reader.get_header(ext=target)
            del header[index]
            header.insert(index, fits.Card.fromstring(previous), useblanks=False)
            self._held_edits.setdefault(target, {})[index] = texts
        self._applied = []
        self.populate_table(self.current_ext)
        self.filter_table(self.search_bar.text())

    @staticmethod
    def _check_card(keyword, value, comment):
        """Raise ValueError, naming the keyword, if astropy would refuse this card."""
        try:
            if keyword in FitsReader.COMMENTARY_KEYWORDS:
                card = fits.Card(keyword, value)
            else:
                card = fits.Card(keyword, value, comment)
            card.verify('exception')
            card.image
        except Exception as exc:
            raise ValueError(f"{keyword or 'blank card'}: {exc}") from exc

    def _mark_table_as_loaded(self, indices):
        """After a write, the table's text for these cards is what the header holds."""
        for row in range(self.table.rowCount()):
            keyword_item = self.table.item(row, 0)
            if keyword_item and keyword_item.data(Qt.ItemDataRole.UserRole) in indices:
                for col in (1, 2):
                    item = self.table.item(row, col)
                    item.setData(Qt.ItemDataRole.UserRole, item.text())

    def save_header(self):
        try:
            # Write the edits held for every extension, the one shown included.
            if not self.apply_table_edits():
                return

            if self._skipped_keywords:
                QMessageBox.information(
                    self,
                    "Some keywords were not changed",
                    "These keywords describe the file's structure or how its data are "
                    "stored, and are managed by the FITS writer, so they were left alone:\n\n    "
                    + ", ".join(sorted(set(self._skipped_keywords)))
                    + "\n\nEditing them would either be discarded on write or change what "
                    "the stored data mean. All other changes were applied.",
                )

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
                    # The confirmation ran its own event loop; check again.
                    if self._refuse_if_file_changed():
                        return
                    try:
                        self.fits_reader.save()
                    except Exception as e:
                        # The edits were applied to the reader's header before the save; a
                        # failed save must not leave them there, where Cancel would no longer
                        # discard them and the next save of any kind would write them.
                        self._unapply()
                        QMessageBox.critical(
                            self, "Error",
                            f"Failed to save header: {e}\n\nThe edits were not applied. "
                            "They are still shown here, unsaved.")
                        return
                    QMessageBox.information(self, "Success", "FITS Header saved successfully.")
                else:
                    QMessageBox.information(self, "Header Updated", "Header cards updated in memory (file not overwritten).")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save header: {str(e)}")
