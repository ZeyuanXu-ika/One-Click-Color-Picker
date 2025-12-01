"""
按键取色器（PyQt5）
功能：
- 按热键取色，默认为 e
- 按热键复制选中颜色到剪贴板，默认为 ctrl+c
- 固定置顶窗口防止被切换窗口遮挡
- 取色历史记录序列（保证历史中每个颜色只保存一次；再次取到同色则移动到队首）
- 热键可调
- 方向键上/下切换历史中选择的颜色
注意：
- 高DPI/多显示器上可能定位有偏差（pyautogui.pixel）
"""
import sys
import threading
from collections import deque
import pyautogui
import pyperclip
import keyboard
from PyQt5 import QtCore, QtGui, QtWidgets

MAX_HISTORY = 10

# 默认热键（可在设置中修改）
DEFAULT_PICK_HOTKEY = "e"
DEFAULT_COPY_HOTKEY = "ctrl+c"

def rgb_to_hex(r, g, b):
    return "#{:02X}{:02X}{:02X}".format(r, g, b)

# diy调整热键
class HotkeySettingsDialog(QtWidgets.QDialog):
    """简单的设置对话框：输入两条热键字符串并保存。"""
    def __init__(self, parent=None, pick_hotkey="", copy_hotkey=""):
        super().__init__(parent)
        self.setWindowTitle("设置热键")
        self.setModal(True)
        self.resize(380, 160)

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.edit_pick = QtWidgets.QLineEdit(pick_hotkey)
        self.edit_copy = QtWidgets.QLineEdit(copy_hotkey)
        self.edit_pick.setPlaceholderText("例如: ctrl+shift+v 或 win+v 或 alt+q")
        self.edit_copy.setPlaceholderText("例如: ctrl+shift+c")
        form.addRow("取色热键:", self.edit_pick)
        form.addRow("复制热键:", self.edit_copy)
        layout.addLayout(form)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_save = QtWidgets.QPushButton("保存")
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def get_values(self):
        return self.edit_pick.text().strip(), self.edit_copy.text().strip()

class ColorPickerWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("按键取色器（可设置热键）")
        self.setFixedSize(420, 460)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)

        # 热键
        self.pick_hotkey = DEFAULT_PICK_HOTKEY
        self.copy_hotkey = DEFAULT_COPY_HOTKEY
        self.pick_handler = None
        self.copy_handler = None
        self.history = deque(maxlen=MAX_HISTORY)

        # current history index (0 == newest/top)
        self.history_index = 0

        # UI
        central = QtWidgets.QWidget()
        # ensure central widget can accept focus so the window receives key events
        central.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(12, 12, 12, 12)
        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(360, 130)
        self.swatch.setFrameShape(QtWidgets.QFrame.Box)
        v.addWidget(self.swatch, alignment=QtCore.Qt.AlignCenter)
        self.label_rgb = QtWidgets.QLabel("RGB: -")
        self.label_hex = QtWidgets.QLabel("HEX: -")
        font = QtGui.QFont()
        font.setPointSize(10)
        self.label_rgb.setFont(font)
        self.label_hex.setFont(font)
        v.addWidget(self.label_rgb)
        v.addWidget(self.label_hex)

        # 按钮区（会动态显示热键文本）
        h = QtWidgets.QHBoxLayout()
        self.btn_pick = QtWidgets.QPushButton()
        self.btn_pick.clicked.connect(self.pick_under_cursor)
        h.addWidget(self.btn_pick)
        self.btn_copy = QtWidgets.QPushButton()
        self.btn_copy.clicked.connect(self.copy_selected_color)
        h.addWidget(self.btn_copy)
        self.btn_settings = QtWidgets.QPushButton("设置热键…")
        self.btn_settings.clicked.connect(self.open_settings)
        h.addWidget(self.btn_settings)
        v.addLayout(h)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("历史 (点击选择):"))
        self.btn_clear = QtWidgets.QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear_history)
        row.addWidget(self.btn_clear)
        v.addLayout(row)
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.itemClicked.connect(self.on_history_clicked)
        v.addWidget(self.list_widget)
        self.status = QtWidgets.QLabel("初始化中…")
        v.addWidget(self.status)
        self.current_rgb = None
        self.selected_rgb = None
        # 把焦点设置到 central，以确保按键事件送达
        central.setFocus()

        threading.Thread(target=self.register_hotkeys, daemon=True).start()
        self.update_button_labels()

    def update_button_labels(self):
        # 把热键显示在按钮上，便于用户查看
        self.btn_pick.setText(f"手动取色 ({self.pick_hotkey})")
        self.btn_copy.setText(f"复制到剪贴板 ({self.copy_hotkey})")

    def register_hotkeys(self):
        """（重）注册全局热键。线程安全地调用 keyboard API；在 UI 线程更新状态。"""
        # 在注册前先尝试移除旧的 handler（如果有）
        try:
            if self.pick_handler:
                keyboard.remove_hotkey(self.pick_handler)
                self.pick_handler = None
        except Exception:
            pass
        try:
            if self.copy_handler:
                keyboard.remove_hotkey(self.copy_handler)
                self.copy_handler = None
        except Exception:
            pass
        errors = []
        try:
            self.pick_handler = keyboard.add_hotkey(self.pick_hotkey, self.pick_under_cursor)
        except Exception as e:
            errors.append(f"注册取色热键失败: {e}")
            self.pick_handler = None
        try:
            self.copy_handler = keyboard.add_hotkey(self.copy_hotkey, self.copy_selected_color)
        except Exception as e:
            errors.append(f"注册复制热键失败: {e}")
            self.copy_handler = None
        if errors:
            txt = "；".join(errors)
            QtCore.QMetaObject.invokeMethod(self, "set_status", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, txt))
            QtCore.QMetaObject.invokeMethod(self, "show_error_box", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, txt))
        else:
            QtCore.QMetaObject.invokeMethod(self, "set_status", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"热键已注册：取色 {self.pick_hotkey} / 复制 {self.copy_hotkey}"))
        QtCore.QMetaObject.invokeMethod(self, "update_button_labels", QtCore.Qt.QueuedConnection)

    @QtCore.pyqtSlot(str)
    def show_error_box(self, text):
        QtWidgets.QMessageBox.warning(self, "热键注册失败", f"{text}\n\n可能原因：热键格式不对或权限不足。试试换一个组合键（例如 ctrl+alt+v）。")

    @QtCore.pyqtSlot(str)
    def set_status(self, text):
        self.status.setText(text)

    def open_settings(self):
        dlg = HotkeySettingsDialog(self, pick_hotkey=self.pick_hotkey, copy_hotkey=self.copy_hotkey)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            new_pick, new_copy = dlg.get_values()
            if not new_pick or not new_copy:
                QtWidgets.QMessageBox.warning(self, "输入错误", "热键不能为空，请输入有效的热键字符串。")
                return
            self.pick_hotkey = new_pick
            self.copy_hotkey = new_copy
            self.update_button_labels()
            threading.Thread(target=self.register_hotkeys, daemon=True).start()

    def pick_under_cursor(self):
        """添加或更新历史：如果取到的颜色已存在于 history，则把它移动到队首（不重复），否则 appendleft"""
        try:
            x, y = pyautogui.position()
            r, g, b = pyautogui.pixel(x, y)  # 可能对高DPI不兼容
            self.current_rgb = (r, g, b)
            hexc = rgb_to_hex(r, g, b)

            # 更新显示
            QtCore.QMetaObject.invokeMethod(self.label_rgb, "setText", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"RGB: {r}, {g}, {b}  (pos: {x},{y})"))
            QtCore.QMetaObject.invokeMethod(self.label_hex, "setText", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"HEX: {hexc}"))
            QtCore.QMetaObject.invokeMethod(self.swatch, "setStyleSheet", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"background-color: {hexc}; border: 1px solid #000;"))

            # 如果颜色已经在历史中，先移除原有项（防止重复），再把该颜色放到队首
            # deque 没有直接的 remove by value 方法在某些 Python 版本不可用，但通常支持 remove()
            try:
                # 如果存在则删除，然后再插入到左侧
                if (r, g, b) in self.history:
                    # 删除旧位置
                    self.history.remove((r, g, b))
                    # 再把它移动到前面
                    self.history.appendleft((r, g, b))
                else:
                    # 新颜色，直接插入到前面
                    self.history.appendleft((r, g, b))
            except Exception:
                # 兼容性回退：手动重建 deque
                lst = [c for c in self.history if c != (r, g, b)]
                lst.insert(0, (r, g, b))
                # 保持最大长度
                lst = lst[:self.history.maxlen]
                self.history = deque(lst, maxlen=self.history.maxlen)

            # 每次新加或更新后把 index 置为 0（最新）
            self.history_index = 0
            QtCore.QMetaObject.invokeMethod(self, "refresh_history_list", QtCore.Qt.QueuedConnection)

            self.selected_rgb = self.history[0] if self.history else self.current_rgb
            QtCore.QMetaObject.invokeMethod(self, "set_status", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"已采样并加入/更新历史: {hexc}"))
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(self, "set_status", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"取色失败: {e}"))

    @QtCore.pyqtSlot()
    def refresh_history_list(self):
        self.list_widget.clear()
        for rgb in self.history:
            hexc = rgb_to_hex(*rgb)
            item = QtWidgets.QListWidgetItem(f"{hexc}    {rgb}")
            pix = QtGui.QPixmap(24, 24)
            pix.fill(QtGui.QColor(*rgb))
            item.setIcon(QtGui.QIcon(pix))
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            # 同步选中到 history_index（防止刷新后丢失选中）
            idx = max(0, min(self.history_index, self.list_widget.count() - 1))
            self.list_widget.setCurrentRow(idx)

    def on_history_clicked(self, item):
        txt = item.text()
        try:
            part = txt.split()[-1]
            r, g, b = part.strip("()").split(",")
            rgb = (int(r), int(g), int(b))
            self.selected_rgb = rgb
            # 更新 history_index 以与键盘控制一致
            self.history_index = self.list_widget.currentRow()
            hexc = rgb_to_hex(*rgb)
            self.status.setText(f"手动选择: {hexc}")
            self.swatch.setStyleSheet(f"background-color: {hexc}; border: 1px solid #000;")
            self.label_hex.setText(f"HEX: {hexc}")
            self.label_rgb.setText(f"RGB: {rgb}")
        except Exception:
            self.status.setText("选择历史项出错")

    def copy_selected_color(self):
        if not self.selected_rgb:
            if self.history:
                rgb = self.history[0]
            elif self.current_rgb:
                rgb = self.current_rgb
            else:
                QtCore.QMetaObject.invokeMethod(self, "set_status", QtCore.Qt.QueuedConnection,
                                                QtCore.Q_ARG(str, "无可复制颜色"))
                return
        else:
            rgb = self.selected_rgb

        hexc = rgb_to_hex(*rgb)
        try:
            pyperclip.copy(hexc)
            QtCore.QMetaObject.invokeMethod(self, "set_status", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"已复制: {hexc}"))
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(self, "set_status", QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, f"复制失败: {e}"))

    def clear_history(self):
        self.history.clear()
        self.list_widget.clear()
        self.selected_rgb = None
        self.history_index = 0
        self.set_status("历史已清空")

    def closeEvent(self, event):
        try:
            if self.pick_handler:
                keyboard.remove_hotkey(self.pick_handler)
            if self.copy_handler:
                keyboard.remove_hotkey(self.copy_handler)
        except Exception:
            pass
        event.accept()

    # ---------- 通过上下键切换历史 ------------
    def select_history_index(self, index: int):
        """根据 history 索引选中并更新 UI。index: 0 为最新（顶部），越大越旧。"""
        if not self.history:
            return
        # clamp
        index = max(0, min(index, len(self.history) - 1))
        self.history_index = index
        rgb = list(self.history)[index]
        self.selected_rgb = rgb
        hexc = rgb_to_hex(*rgb)
        # 更新 UI
        self.swatch.setStyleSheet(f"background-color: {hexc}; border: 1px solid #000;")
        self.label_hex.setText(f"HEX: {hexc}")
        self.label_rgb.setText(f"RGB: {rgb}")
        # 同步 QListWidget 选中行（注意 QListWidget 行号与 history index 一致）
        self.list_widget.setCurrentRow(index)
        self.set_status(f"已切换历史: {hexc}")

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        """捕获方向键: Up -> older (index+1), Down -> newer (index-1).
        改动：当移动到队尾或队首时进行循环（wrap），不会停止。"""
        key = event.key()
        if not self.history:
            # 没有历史时交给父类处理（或什么也不做）
            return super().keyPressEvent(event)

        n = len(self.history)

        if key == QtCore.Qt.Key_Up:
            # move to older color (increase index), wrap around
            new_index = (self.history_index - 1) % n
            self.select_history_index(new_index)
        elif key == QtCore.Qt.Key_Down:
            # move to newer color (decrease index), wrap around
            new_index = (self.history_index + 1) % n
            self.select_history_index(new_index)
        else:
            super().keyPressEvent(event)


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = ColorPickerWindow()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    # 参数设置（可在运行前修改）
    MAX_HISTORY = 10  # 历史保存颜色个数
    DEFAULT_PICK_HOTKEY = "e"  # 取色快捷键
    DEFAULT_COPY_HOTKEY = "ctrl+c"  # 复制快捷键
    main()
