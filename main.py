import sys
import subprocess
import os
import gc
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QScrollArea, QColorDialog, 
                             QLabel, QMessageBox, QFrame, QFileDialog, QProgressDialog,
                             QSlider, QInputDialog) 
from PyQt5.QtGui import QPainter, QPen, QPixmap, QPalette, QColor, QCursor, QIcon, QImage, QFont
from PyQt5.QtCore import Qt, QPoint, QRect, QSize, pyqtSignal, QBuffer, QByteArray, QIODevice
from PyQt5.QtPrintSupport import QPrinter

# --- OPTIONAL IMPORTS ---
try:
    from pdf2image import convert_from_path
    PDF_IMPORT_AVAILABLE = True
except ImportError:
    PDF_IMPORT_AVAILABLE = False

# --- CONSTANTS ---
IMG_WIDTH = 1588
IMG_HEIGHT = 2246
VIEW_SCALE = 2 
VIEW_WIDTH = IMG_WIDTH // VIEW_SCALE
VIEW_HEIGHT = IMG_HEIGHT // VIEW_SCALE
UNDO_LIMIT = 5 

class Page:
    """Represents a single A4 page."""
    def __init__(self, pixmap=None):
        if pixmap:
            self.high_res_pixmap = pixmap
        else:
            self.high_res_pixmap = QPixmap(IMG_WIDTH, IMG_HEIGHT)
            self.high_res_pixmap.fill(Qt.white)
            
        self.compressed_data = None
        self.preview_pixmap = self.high_res_pixmap.scaled(
            VIEW_WIDTH, VIEW_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.is_compressed = False

    def compress(self):
        """Converts High Res QPixmap to PNG bytes (Lossless) to save RAM."""
        if self.is_compressed: return
        self.preview_pixmap = self.high_res_pixmap.scaled(
            VIEW_WIDTH, VIEW_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        painter = QPainter(self.preview_pixmap)
        painter.setPen(QPen(Qt.red))
        painter.setFont(QFont("Arial", 14, QFont.Bold))
        rect = self.preview_pixmap.rect().adjusted(0, 5, -5, 0)
        painter.drawText(rect, Qt.AlignRight | Qt.AlignTop, "Compressed (PNG)")
        painter.end()
        ba = QByteArray()
        buff = QBuffer(ba)
        buff.open(QIODevice.WriteOnly)
        self.high_res_pixmap.save(buff, "PNG") 
        self.compressed_data = ba.data()
        self.high_res_pixmap = None
        self.is_compressed = True

    def decompress(self):
        """Restores High Res QPixmap from bytes."""
        if not self.is_compressed: return
        img = QImage.fromData(self.compressed_data, "PNG")
        self.high_res_pixmap = QPixmap.fromImage(img)
        self.compressed_data = None
        self.is_compressed = False

    def clone(self):
        new_page = Page()
        new_page.is_compressed = self.is_compressed
        if self.is_compressed:
            new_page.compressed_data = self.compressed_data
            new_page.preview_pixmap = self.preview_pixmap
            new_page.high_res_pixmap = None
        else:
            new_page.high_res_pixmap = self.high_res_pixmap.copy()
            new_page.preview_pixmap = self.preview_pixmap.copy()
        return new_page

class SnippingTool(QWidget):
    snippet_captured = pyqtSignal(QPixmap)
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setWindowState(Qt.WindowFullScreen)
        self.setCursor(Qt.CrossCursor)
        screen = QApplication.primaryScreen()
        self.original_pixmap = screen.grabWindow(0) if screen else QPixmap()
        self.start_point = QPoint(); self.end_point = QPoint(); self.is_selecting = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.original_pixmap)
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.setPen(Qt.NoPen); painter.drawRect(self.rect())
        if self.is_selecting:
            rect = QRect(self.start_point, self.end_point).normalized()
            painter.drawPixmap(rect, self.original_pixmap, rect)
            painter.setPen(QPen(Qt.red, 3)); painter.drawRect(rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_point = event.pos(); self.end_point = event.pos(); self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting: self.end_point = event.pos(); self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_selecting = False
            rect = QRect(self.start_point, self.end_point).normalized()
            if rect.width() > 5 and rect.height() > 5:
                self.snippet_captured.emit(self.original_pixmap.copy(rect))
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape: self.close()

class Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__() 
        self.setParent(parent)
        self.setAttribute(Qt.WA_StaticContents)
        self.setMouseTracking(True)
        self.pages = [] 
        self.active_page_index = 0
        self.zoom_factor = 1.0
        self.zoom_mode = False
        self.panning = False
        self.pan_start_pos = QPoint()
        self.scroll_area = None
        self.update_widget_size()
        self.undo_stack = []
        self.drawing = False
        self.last_point_img = QPoint() 
        self.current_tool = 'pen' 
        self.brush_size = 8 
        self.text_size = 24  # New text size variable
        self.brush_color = Qt.black
        self.is_selecting = False       
        self.floating_pixmap = None     
        self.floating_pos_img = QPoint()    

    def update_widget_size(self):
        self.setFixedSize(int(VIEW_WIDTH * self.zoom_factor), int(len(self.pages) * VIEW_HEIGHT * self.zoom_factor))

    def to_image_coords(self, pos):
        return QPoint(int(pos.x() / self.zoom_factor * VIEW_SCALE), int(pos.y() / self.zoom_factor * VIEW_SCALE))

    def get_page_at(self, global_y):
        index = max(0, min(int(global_y // IMG_HEIGHT), len(self.pages) - 1))
        return index, int(global_y % IMG_HEIGHT)

    def set_brush_size(self, size): self.brush_size = size
    
    def set_text_size(self, size): self.text_size = size # New setter

    def force_gc(self):
        gc.collect()
        print("Garbage Collected manually.")

    def save_state(self):
        if len(self.undo_stack) >= UNDO_LIMIT: self.undo_stack.pop(0)
        self.undo_stack.append([p.clone() for p in self.pages])

    def undo(self):
        if self.undo_stack:
            self.pages = self.undo_stack.pop()
            self.active_page_index = next((i for i, p in enumerate(self.pages) if not p.is_compressed), 0)
            self.pages[self.active_page_index].decompress()
            self.update_widget_size(); self.update(); gc.collect()

    def reset_to_a4(self):
        self.undo_stack.clear(); gc.collect()
        self.pages = [Page()]; self.active_page_index = 0
        self.update_widget_size(); self.update()

    def add_page(self):
        self.save_state()
        if 0 <= self.active_page_index < len(self.pages): self.pages[self.active_page_index].compress()
        self.pages.append(Page())
        self.active_page_index = len(self.pages) - 1
        self.update_widget_size(); self.update(); self.auto_save()

    def set_zoom_mode(self, enabled):
        self.zoom_mode = enabled
        if enabled:
            self.setCursor(Qt.OpenHandCursor)
        else:
            if self.current_tool in ['pen', 'eraser']:
                self.setCursor(Qt.ArrowCursor)
            elif self.current_tool == 'select_box':
                self.setCursor(Qt.CrossCursor)
            elif self.current_tool == 'text':
                self.setCursor(Qt.IBeamCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def zoom_in(self):
        self.set_zoom(self.zoom_factor * 1.2)

    def zoom_out(self):
        self.set_zoom(self.zoom_factor / 1.2)

    def set_zoom(self, factor, widget_pos=None):
        old_factor = self.zoom_factor
        self.zoom_factor = max(0.1, min(factor, 10.0))
        
        if self.scroll_area:
            scrollbar_h = self.scroll_area.horizontalScrollBar()
            scrollbar_v = self.scroll_area.verticalScrollBar()
            
            if widget_pos is None:
                viewport = self.scroll_area.viewport()
                widget_pos = QPoint(
                    scrollbar_h.value() + viewport.width() // 2,
                    scrollbar_v.value() + viewport.height() // 2
                )
            
            new_x = widget_pos.x() * (self.zoom_factor / old_factor)
            new_y = widget_pos.y() * (self.zoom_factor / old_factor)
            
            viewport_x = widget_pos.x() - scrollbar_h.value()
            viewport_y = widget_pos.y() - scrollbar_v.value()
            
            self.update_widget_size()
            
            scrollbar_h.setValue(int(new_x - viewport_x))
            scrollbar_v.setValue(int(new_y - viewport_y))
        else:
            self.update_widget_size()
            
        self.update()

    def set_pen_color(self, color):
        self.paste_floating_selection(); self.current_tool = 'pen'
        self.brush_color = color
        if not self.zoom_mode: self.setCursor(Qt.ArrowCursor)

    def set_eraser(self):
        self.paste_floating_selection(); self.current_tool = 'eraser'
        if not self.zoom_mode: self.setCursor(Qt.ArrowCursor)

    def set_move_tool(self):
        self.paste_floating_selection(); self.current_tool = 'select_box'
        if not self.zoom_mode: self.setCursor(Qt.CrossCursor)

    def set_text_tool(self): # New tool initializer
        self.paste_floating_selection(); self.current_tool = 'text'
        if not self.zoom_mode: self.setCursor(Qt.IBeamCursor)

    def paste_floating_selection(self):
        if self.floating_pixmap:
            page_idx, _ = self.get_page_at(self.floating_pos_img.y() + (self.floating_pixmap.height() // 2))
            if page_idx != self.active_page_index:
                self.pages[self.active_page_index].compress()
                self.pages[page_idx].decompress(); self.active_page_index = page_idx
            page = self.pages[page_idx]
            painter = QPainter(page.high_res_pixmap)
            painter.drawPixmap(QPoint(self.floating_pos_img.x(), self.floating_pos_img.y() - (page_idx * IMG_HEIGHT)), self.floating_pixmap)
            painter.end(); self.floating_pixmap = None; self.update()

    def paste_external_image(self, pixmap):
        self.paste_floating_selection(); self.save_state()
        self.floating_pixmap = pixmap.scaled(pixmap.width() * 2, pixmap.height() * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.current_tool = 'moving_selection'
        self.floating_pos_img = QPoint(100, (len(self.pages) - 1) * IMG_HEIGHT + 100)
        self.update()

    def wheelEvent(self, event):
        if self.zoom_mode:
            if event.angleDelta().y() > 0:
                self.set_zoom(self.zoom_factor * 1.2, event.pos())
            else:
                self.set_zoom(self.zoom_factor / 1.2, event.pos())
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if self.zoom_mode:
            if event.button() == Qt.LeftButton:
                self.panning = True
                self.pan_start_pos = event.globalPos()
                self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() == Qt.LeftButton:
            global_pos = self.to_image_coords(event.pos())
            page_idx, local_y = self.get_page_at(global_pos.y())
            if page_idx != self.active_page_index:
                self.pages[self.active_page_index].compress()
                self.pages[page_idx].decompress(); self.active_page_index = page_idx; self.update()

            if self.current_tool == 'text': # Logic for typing
                text, ok = QInputDialog.getMultiLineText(self, "Insert Text", "Type text to place at cursor:")
                if ok and text:
                    self.save_state()
                    page = self.pages[page_idx]
                    painter = QPainter(page.high_res_pixmap)
                    painter.setPen(self.brush_color)
                    painter.setFont(QFont("Times New Roman", self.text_size))
                    painter.drawText(global_pos.x(), local_y, text)
                    painter.end(); self.update()
            elif self.current_tool == 'select_box':
                self.is_selecting = True; self.select_start_img = global_pos; self.select_current_img = global_pos
            elif self.current_tool == 'moving_selection':
                self.paste_floating_selection(); self.current_tool = 'select_box' 
            elif self.current_tool in ['pen', 'eraser']:
                self.save_state(); self.drawing = True; self.last_point_img = global_pos

    def mouseMoveEvent(self, event):
        if self.zoom_mode:
            if self.panning and self.scroll_area:
                delta = event.globalPos() - self.pan_start_pos
                self.pan_start_pos = event.globalPos()
                
                scrollbar_h = self.scroll_area.horizontalScrollBar()
                scrollbar_v = self.scroll_area.verticalScrollBar()
                
                scrollbar_h.setValue(scrollbar_h.value() - delta.x())
                scrollbar_v.setValue(scrollbar_v.value() - delta.y())
            return

        global_pos = self.to_image_coords(event.pos())
        if self.current_tool == 'moving_selection' and self.floating_pixmap:
            self.floating_pos_img = global_pos - QPoint(self.floating_pixmap.width() // 2, self.floating_pixmap.height() // 2)
            self.update(); return
        if (event.buttons() & Qt.LeftButton):
            if self.current_tool == 'select_box' and self.is_selecting:
                self.select_current_img = global_pos; self.update() 
            elif self.current_tool in ['pen', 'eraser'] and self.drawing:
                page_idx, local_y = self.get_page_at(global_pos.y())
                if page_idx == self.active_page_index:
                    page = self.pages[page_idx]
                    painter = QPainter(page.high_res_pixmap)
                    width = self.brush_size * 5 if self.current_tool == 'eraser' else self.brush_size
                    painter.setPen(QPen(Qt.white if self.current_tool == 'eraser' else self.brush_color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    painter.drawLine(QPoint(self.last_point_img.x(), int(self.last_point_img.y() % IMG_HEIGHT)), QPoint(global_pos.x(), local_y))
                    painter.end()
                self.last_point_img = global_pos; self.update()

    def mouseReleaseEvent(self, event):
        if self.zoom_mode:
            if event.button() == Qt.LeftButton:
                self.panning = False
                self.setCursor(Qt.OpenHandCursor)
            return

        if event.button() == Qt.LeftButton:
            global_pos = self.to_image_coords(event.pos())
            if self.current_tool == 'select_box' and self.is_selecting:
                self.is_selecting = False
                rect = QRect(self.select_start_img, self.select_current_img).normalized()
                if rect.width() > 10 and rect.height() > 10:
                    page_idx, local_y_start = self.get_page_at(rect.top())
                    if page_idx != self.active_page_index:
                         self.pages[self.active_page_index].compress()
                         self.pages[page_idx].decompress(); self.active_page_index = page_idx
                    page = self.pages[page_idx]
                    local_rect = QRect(rect.x(), local_y_start, rect.width(), rect.height()).intersected(QRect(0, 0, IMG_WIDTH, IMG_HEIGHT))
                    if not local_rect.isEmpty():
                        self.save_state(); self.floating_pixmap = page.high_res_pixmap.copy(local_rect)
                        painter = QPainter(page.high_res_pixmap); painter.fillRect(local_rect, Qt.white); painter.end()
                        self.current_tool = 'moving_selection'
                        self.floating_pos_img = global_pos - QPoint(self.floating_pixmap.width() // 2, self.floating_pixmap.height() // 2)
                        self.update()
            elif self.current_tool in ['pen', 'eraser']:
                self.drawing = False
                if 0 <= self.active_page_index < len(self.pages):
                     self.pages[self.active_page_index].preview_pixmap = self.pages[self.active_page_index].high_res_pixmap.scaled(VIEW_WIDTH, VIEW_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.scale(self.zoom_factor, self.zoom_factor)
        rect = event.rect()
        
        unscaled_top = rect.top() / self.zoom_factor
        unscaled_bottom = rect.bottom() / self.zoom_factor
        
        start_p = max(0, int(unscaled_top // VIEW_HEIGHT))
        end_p = min(len(self.pages) - 1, int(unscaled_bottom // VIEW_HEIGHT))
        
        for i in range(start_p, end_p + 1):
            page, y_pos = self.pages[i], i * VIEW_HEIGHT
            if page.is_compressed: painter.drawPixmap(0, y_pos, page.preview_pixmap)
            else: painter.drawPixmap(QRect(0, y_pos, VIEW_WIDTH, VIEW_HEIGHT), page.high_res_pixmap, page.high_res_pixmap.rect())
            if i > 0:
                painter.setPen(QPen(Qt.gray, 2, Qt.DashLine)); painter.drawLine(0, y_pos, VIEW_WIDTH, y_pos)
        if self.current_tool == 'select_box' and self.is_selecting:
            painter.setPen(QPen(Qt.blue, 2, Qt.DashLine))
            painter.drawRect(QRect(self.select_start_img / VIEW_SCALE, self.select_current_img / VIEW_SCALE).normalized())
        if self.current_tool == 'moving_selection' and self.floating_pixmap:
            screen_pos = self.floating_pos_img / VIEW_SCALE
            display_float = self.floating_pixmap.scaled(self.floating_pixmap.width() // VIEW_SCALE, self.floating_pixmap.height() // VIEW_SCALE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(screen_pos, display_float)
            painter.setPen(QPen(Qt.blue, 1, Qt.DotLine)); painter.drawRect(screen_pos.x(), screen_pos.y(), display_float.width(), display_float.height())

    def import_pdf(self):
        if not PDF_IMPORT_AVAILABLE:
            QMessageBox.critical(self, "Error", "pdf2image required."); return
        filename, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", "PDF Files (*.pdf)")
        if not filename: return
        progress = QProgressDialog("Importing PDF...", "Cancel", 0, 100, self); progress.show()
        try:
            pages_img = convert_from_path(filename, dpi=200); self.save_state()
            if 0 <= self.active_page_index < len(self.pages): self.pages[self.active_page_index].compress()
            for page_pil in pages_img:
                qimg = QImage(page_pil.convert("RGBA").tobytes("raw", "RGBA"), page_pil.size[0], page_pil.size[1], QImage.Format_RGBA8888)
                scaled_qimg = qimg.scaledToWidth(IMG_WIDTH, Qt.SmoothTransformation)            
                final_page_pix = QPixmap(IMG_WIDTH, IMG_HEIGHT); final_page_pix.fill(Qt.white)
                p = QPainter(final_page_pix); p.drawPixmap(0, 0, QPixmap.fromImage(scaled_qimg)); p.end()
                p_obj = Page(final_page_pix); p_obj.compress(); self.pages.append(p_obj)
            self.active_page_index = len(self.pages) - 1; self.pages[self.active_page_index].decompress()
            self.update_widget_size(); self.update()
        except Exception as e: QMessageBox.critical(self, "Error", f"Failed: {str(e)}")
        finally: progress.close(); gc.collect()

    def auto_save(self):
        filename = os.getcwd() + '/back_up.pdf'
        self.save_pdf_engine(filename, show_msg=False)

    def save_pdf_high_res(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save PDF", datetime.now().strftime("%Y-%m-%d") + ".pdf", "PDF Files (*.pdf)")
        if filename: self.save_pdf_engine(filename, show_msg=True)

    def save_pdf_engine(self, filename, show_msg=True): # Optimized Lossless PDF Engine
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat); printer.setOutputFileName(filename)
        printer.setPageSize(QPrinter.A4); printer.setFullPage(True)
        painter = QPainter(printer); rect = printer.pageRect()
        for i, page in enumerate(self.pages):
            if i > 0: printer.newPage()
            was_c = page.is_compressed
            if was_c: page.decompress()
            # Converting to QImage triggers lossless Flate compression in the PDF engine
            painter.drawImage(rect, page.high_res_pixmap.toImage())
            if was_c: page.compress()
        painter.end(); gc.collect()
        if show_msg: QMessageBox.information(self, "Success", "Saved (Lossless Compressed)!")

class NotepadApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scrap Paper")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, 'app_icon.png')
        if os.path.exists(icon_path): self.setWindowIcon(QIcon(icon_path))
        self.setGeometry(100, 100, 1200, 900)
        self.pinned = False; self.canvas = Canvas()
        main_widget = QWidget(); self.setCentralWidget(main_widget); layout = QHBoxLayout(main_widget)
        sidebar = QVBoxLayout(); sidebar.setAlignment(Qt.AlignTop); sidebar.addWidget(QLabel("<b>Tools</b>"))
        
        btn_undo = QPushButton("↶ Undo"); btn_undo.setStyleSheet("background-color: #ffdddd;")
        btn_undo.clicked.connect(self.canvas.undo); sidebar.addWidget(btn_undo)
        
        sidebar.addWidget(QLabel("Thickness:"))
        slider = QSlider(Qt.Horizontal); slider.setRange(2, 60); slider.setValue(8)
        slider.valueChanged.connect(self.canvas.set_brush_size); sidebar.addWidget(slider)

        # TEXT TOOL ROW
        text_row = QHBoxLayout()
        btn_type = QPushButton("Type")
        btn_type.clicked.connect(self.canvas.set_text_tool)
        t_slider = QSlider(Qt.Horizontal)
        t_slider.setRange(8, 120); t_slider.setValue(24); t_slider.setToolTip("Font Size")
        t_slider.valueChanged.connect(self.canvas.set_text_size)
        text_row.addWidget(btn_type); text_row.addWidget(t_slider)
        sidebar.addLayout(text_row)

        btn_black = QPushButton("Black Pen"); btn_black.clicked.connect(lambda: self.canvas.set_pen_color(Qt.black)); sidebar.addWidget(btn_black)
        btn_blue = QPushButton("Blue Pen"); btn_blue.setStyleSheet("color: blue;"); btn_blue.clicked.connect(lambda: self.canvas.set_pen_color(Qt.blue)); sidebar.addWidget(btn_blue)
        btn_red = QPushButton("Red Pen"); btn_red.setStyleSheet("color: red;"); btn_red.clicked.connect(lambda: self.canvas.set_pen_color(Qt.red)); sidebar.addWidget(btn_red)
        btn_color = QPushButton("Pick Color..."); btn_color.clicked.connect(self.choose_color); sidebar.addWidget(btn_color)
        btn_eraser = QPushButton("Eraser"); btn_eraser.clicked.connect(self.canvas.set_eraser); sidebar.addWidget(btn_eraser)
        btn_move = QPushButton("✂ Cut & Move"); btn_move.setStyleSheet("background-color: #e0e0e0;"); btn_move.clicked.connect(self.canvas.set_move_tool); sidebar.addWidget(btn_move)

        self.add_separator(sidebar); sidebar.addWidget(QLabel("<b>Input</b>"))
        btn_add_page = QPushButton("+ Add A4 Page"); btn_add_page.clicked.connect(self.canvas.add_page); sidebar.addWidget(btn_add_page)
        btn_grab = QPushButton("📷 Screen Grab"); btn_grab.clicked.connect(self.start_screen_grab); sidebar.addWidget(btn_grab)
        btn_import = QPushButton("Import PDF"); btn_import.clicked.connect(self.canvas.import_pdf); sidebar.addWidget(btn_import)
        btn_pdf = QPushButton("Save PDF"); btn_pdf.clicked.connect(self.canvas.save_pdf_high_res); sidebar.addWidget(btn_pdf)
        btn_clear = QPushButton("Reset / Clear"); btn_clear.clicked.connect(self.canvas.reset_to_a4); sidebar.addWidget(btn_clear)

        self.add_separator(sidebar); sidebar.addWidget(QLabel("<b>View</b>"))
        self.btn_zoom_mode = QPushButton("Zoom Mode: OFF")
        self.btn_zoom_mode.setCheckable(True)
        self.btn_zoom_mode.clicked.connect(self.toggle_zoom_mode)
        sidebar.addWidget(self.btn_zoom_mode)
        
        btn_zoom_in = QPushButton("Zoom In (+)")
        btn_zoom_in.clicked.connect(self.canvas.zoom_in)
        sidebar.addWidget(btn_zoom_in)
        
        btn_zoom_out = QPushButton("Zoom Out (-)")
        btn_zoom_out.clicked.connect(self.canvas.zoom_out)
        sidebar.addWidget(btn_zoom_out)

        self.add_separator(sidebar); sidebar.addWidget(QLabel("<b>System</b>"))
        btn_gc = QPushButton("⚡ Compact RAM"); btn_gc.clicked.connect(lambda: self.canvas.force_gc()); sidebar.addWidget(btn_gc)
        self.script_disable_path = "./disable_tablet_mode.sh"; self.script_enable_path = "./enable_tablet_mode.sh"
        btn_dis_tab = QPushButton("Disable Tablet Mode"); btn_dis_tab.clicked.connect(lambda: self.run_script(self.script_disable_path)); sidebar.addWidget(btn_dis_tab)
        btn_en_tab = QPushButton("Enable Tablet Mode"); btn_en_tab.clicked.connect(lambda: self.run_script(self.script_enable_path)); sidebar.addWidget(btn_en_tab)
        self.btn_pin = QPushButton("Pin on Top: OFF"); self.btn_pin.setCheckable(True); self.btn_pin.clicked.connect(self.toggle_pin); sidebar.addWidget(self.btn_pin)

        frame_sidebar = QFrame(); frame_sidebar.setLayout(sidebar); frame_sidebar.setFixedWidth(175); layout.addWidget(frame_sidebar)
        self.scroll_area = QScrollArea(); self.scroll_area.setBackgroundRole(QPalette.Dark); self.scroll_area.setStyleSheet("background-color: #ccc;") 
        self.scroll_area.setWidget(self.canvas); self.scroll_area.setWidgetResizable(True); self.scroll_area.setAlignment(Qt.AlignHCenter); layout.addWidget(self.scroll_area)
        self.canvas.scroll_area = self.scroll_area

    def toggle_zoom_mode(self):
        is_checked = self.btn_zoom_mode.isChecked()
        self.btn_zoom_mode.setText(f"Zoom Mode: {'ON' if is_checked else 'OFF'}")
        self.btn_zoom_mode.setStyleSheet("background-color: #aaffaa" if is_checked else "")
        self.canvas.set_zoom_mode(is_checked)

    def should_close(self):
        return QMessageBox.question(self, 'Confirmation', 'Do you want to close?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def closeEvent(self, event):
        if self.should_close(): self.on_close(); event.accept()
        else: event.ignore()

    def on_close(self): print("Window is closing — protocol executed")

    def add_separator(self, layout):
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken); layout.addWidget(line)

    def choose_color(self):
        color = QColorDialog.getColor()
        if color.isValid(): self.canvas.set_pen_color(color)

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint if self.pinned else self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.btn_pin.setText(f"Pin on Top: {'ON' if self.pinned else 'OFF'}")
        self.btn_pin.setStyleSheet("background-color: #aaffaa" if self.pinned else ""); self.show()

    def run_script(self, script_path):
        if not os.path.exists(script_path): QMessageBox.warning(self, "Error", f"Script not found:\n{script_path}"); return
        try: subprocess.Popen(['/bin/bash', script_path])
        except Exception as e: QMessageBox.critical(self, "Error", f"Failed: {str(e)}")
    
    def start_screen_grab(self):
        self.hide(); self.snipper = SnippingTool()
        self.snipper.snippet_captured.connect(self.finish_screen_grab); self.snipper.show()
        
    def finish_screen_grab(self, pixmap):
        self.showNormal(); self.canvas.paste_external_image(pixmap)

if __name__ == '__main__':
    app = QApplication(sys.argv); window = NotepadApp(); window.show(); sys.exit(app.exec_())
