import sys
import os
from PIL import Image
import imagehash

from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton, QFileDialog,
    QVBoxLayout, QLabel, QScrollArea, QCheckBox,
    QMessageBox, QGroupBox, QHBoxLayout,
    QListView, QTreeView
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt

SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
HASH_THRESHOLD = 8
THUMB_SIZE = 140


class DuplicateImageTool(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tool lọc ảnh trùng (multi-folder)")
        self.resize(1200, 800)

        self.image_hashes = {}
        self.checkboxes = []

        layout = QVBoxLayout(self)

        self.btn_select = QPushButton("📁 Chọn NHIỀU folder ảnh")
        self.btn_select.clicked.connect(self.select_folders)

        self.btn_delete = QPushButton("🗑️ Xóa ảnh đã chọn")
        self.btn_delete.clicked.connect(self.delete_selected)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll.setWidget(self.scroll_widget)

        layout.addWidget(self.btn_select)
        layout.addWidget(self.scroll)
        layout.addWidget(self.btn_delete)

    # ================== CHỌN NHIỀU FOLDER (FIX CHUẨN) ==================
    def select_folders(self):
        dialog = QFileDialog(self, "Chọn nhiều folder ảnh")
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)

        # FIX chuẩn: chỉ set MultiSelection cho đúng View
        for view in dialog.findChildren(QListView):
            view.setSelectionMode(QListView.MultiSelection)

        for view in dialog.findChildren(QTreeView):
            view.setSelectionMode(QTreeView.MultiSelection)

        if dialog.exec_():
            folders = dialog.selectedFiles()
            if folders:
                self.scan_images(folders)

    # ================== QUÉT ẢNH ==================
    def scan_images(self, folders):
        self.clear_ui()
        self.image_hashes.clear()

        image_files = []

        for folder in folders:
            for root, _, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith(SUPPORTED_EXT):
                        image_files.append(os.path.join(root, f))

        if not image_files:
            QMessageBox.information(self, "Thông báo", "Không có ảnh")
            return

        for path in image_files:
            try:
                img = Image.open(path).convert("RGB")
                self.image_hashes[path] = imagehash.phash(img)
            except:
                pass

        visited = set()
        groups = []

        for img1, hash1 in self.image_hashes.items():
            if img1 in visited:
                continue

            group = [img1]
            visited.add(img1)

            for img2, hash2 in self.image_hashes.items():
                if img2 in visited:
                    continue
                if abs(hash1 - hash2) <= HASH_THRESHOLD:
                    group.append(img2)
                    visited.add(img2)

            if len(group) > 1:
                groups.append(group)

        if not groups:
            QMessageBox.information(self, "Kết quả", "Không tìm thấy ảnh trùng")
            return

        self.show_groups(groups)

    # ================== HIỂN THỊ NHÓM ẢNH ==================
    def show_groups(self, groups):
        for i, group in enumerate(groups, 1):
            box = QGroupBox(f"Nhóm ảnh trùng #{i} ({len(group)} ảnh)")
            vbox = QVBoxLayout()

            for path in group:
                hbox = QHBoxLayout()

                img_label = QLabel()
                pixmap = QPixmap(path)
                pixmap = pixmap.scaled(
                    THUMB_SIZE, THUMB_SIZE,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                img_label.setPixmap(pixmap)

                cb = QCheckBox(os.path.basename(path))
                cb.full_path = path
                self.checkboxes.append(cb)

                path_label = QLabel(path)
                path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

                hbox.addWidget(img_label)
                hbox.addWidget(cb)
                hbox.addWidget(path_label)

                vbox.addLayout(hbox)

            box.setLayout(vbox)
            self.scroll_layout.addWidget(box)

        self.scroll_layout.addStretch()

    # ================== XÓA ẢNH ==================
    def delete_selected(self):
        to_delete = [cb.full_path for cb in self.checkboxes if cb.isChecked()]

        if not to_delete:
            QMessageBox.warning(self, "Cảnh báo", "Chưa chọn ảnh nào")
            return

        confirm = QMessageBox.question(
            self,
            "Xác nhận",
            f"Bạn chắc chắn xóa {len(to_delete)} ảnh?",
            QMessageBox.Yes | QMessageBox.No
        )

        if confirm != QMessageBox.Yes:
            return

        deleted = 0
        for path in to_delete:
            try:
                os.remove(path)
                deleted += 1
            except:
                pass

        QMessageBox.information(self, "Hoàn tất", f"Đã xóa {deleted} ảnh")
        self.clear_ui()

    # ================== CLEAR UI ==================
    def clear_ui(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.checkboxes.clear()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = DuplicateImageTool()
    w.show()
    sys.exit(app.exec_())