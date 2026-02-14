import sys
import subprocess
import os
import gc
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QScrollArea, QColorDialog, 
                             QLabel, QMessageBox, QFrame, QFileDialog, QProgressDialog,
                             QSlider) 
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
# High Resolution Storage (approx 200 DPI)
IMG_WIDTH = 1588
IMG_HEIGHT = 2246

# View Resolution (Screen View - approx 96 DPI)
VIEW_SCALE = 2 
VIEW_WIDTH = IMG_WIDTH // VIEW_SCALE
VIEW_HEIGHT = IMG_HEIGHT // VIEW_SCALE

# Keep Undo limit at 5 (Smart Undo will keep RAM usage efficient)
UNDO_LIMIT = 5 

class Page:
    """
    Represents a single A4 page.
    """
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
        if self.is_compressed:
            return

        # 1. Update preview
        self.preview_pixmap = self.high_res_pixmap.scaled(
            VIEW_WIDTH, VIEW_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        
        # Draw "Compressed" Watermark on preview
        painter = QPainter(self.preview_pixmap)
        painter.setPen(QPen(Qt.red))
        font = QFont("Arial", 14, QFont.Bold)
        painter.setFont(font)
        rect = self.preview_pixmap.rect().adjusted(0, 5, -5, 0)
        painter.drawText(rect, Qt.AlignRight | Qt.AlignTop, "Compressed (PNG)")
        painter.end()

        # 2. Save to PNG (Lossless)
        ba = QByteArray()
        buff = QBuffer(ba)
        buff.open(QIODevice.WriteOnly)
        self.high_res_pixmap.save(buff, "PNG") 
        self.compressed_data = ba.data()
        
        # 3. Dump heavy object
        self.high_res_pixmap = None
        self.is_compressed = True

    def decompress(self):
        """Restores High Res QPixmap from bytes."""
        if not self.is_compressed:
            return

        img = QImage.fromData(self.compressed_data, "PNG")
        self.high_res_pixmap = QPixmap.fromImage(img)
        
        self.compressed_data = None
        self.is_compressed = False

    def clone(self):
        """
        Smart Clone for Undo:
        If page is compressed (inactive), we SHARE the data reference.
        We only deep-copy active pages.
        """
        new_page = Page()
        new_page.is_compressed = self.is_compressed
        
        if self.is_compressed:
            # REFERENCE SHARING (Fast & Low RAM)
            new_page.compressed_data = self.compressed_data
            new_page.preview_pixmap = self.preview_pixmap # Share preview too
            new_page.high_res_pixmap = None
        else:
            # DEEP COPY (Active page needs its own memory)
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
        if screen:
            self.original_pixmap = screen.grabWindow(0)
        else:
            self.original_pixmap = QPixmap()
            
        self.start_point = QPoint()
        self.end_point = QPoint()
        self.is_selecting = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.original_pixmap)
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.setPen(Qt.NoPen)
        painter.drawRect(self.rect())
        
        if self.is_selecting:
            rect = QRect(self.start_point, self.end_point).normalized()
            painter.drawPixmap(rect, self.original_pixmap, rect)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(Qt.red, 3))
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_point = event.pos()
            self.end_point = event.pos()
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_point = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_selecting = False
            rect = QRect(self.start_point, self.end_point).normalized()
            if rect.width() > 5 and rect.height() > 5:
                cropped = self.original_pixmap.copy(rect)
                self.snippet_captured.emit(cropped)
                self.close()
            else:
                self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

class Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__() 
        self.setParent(parent)
        self.setAttribute(Qt.WA_StaticContents)
        self.setMouseTracking(True)
        
        self.pages = []
        #self.pages = [Page()] 
        
        self.active_page_index = 0
        
        self.update_widget_size()
        
        self.undo_stack = []
        
        self.drawing = False
        self.last_point_img = QPoint() 
        
        self.current_tool = 'pen' 
        self.brush_size = 8 
        self.brush_color = Qt.black

        self.select_start_img = QPoint()
        self.select_current_img = QPoint()
        self.is_selecting = False       
        self.floating_pixmap = None     
        self.floating_pos_img = QPoint()    

    def update_widget_size(self):
        total_h = len(self.pages) * VIEW_HEIGHT
        self.setFixedSize(VIEW_WIDTH, total_h)

    def to_image_coords(self, pos):
        global_x = pos.x() * VIEW_SCALE
        global_y = pos.y() * VIEW_SCALE
        return QPoint(global_x, global_y)

    def get_page_at(self, global_y):
        index = int(global_y // IMG_HEIGHT)
        local_y = int(global_y % IMG_HEIGHT)
        if index < 0: index = 0
        if index >= len(self.pages): index = len(self.pages) - 1
        return index, local_y

    def set_brush_size(self, size):
        self.brush_size = size

    def force_gc(self):
        """Manually trigger garbage collection."""
        gc.collect()
        print("Garbage Collected manually.")

    def save_state(self):
        if len(self.undo_stack) >= UNDO_LIMIT:
            self.undo_stack.pop(0)
        
        # Clone pages (smart clone uses references for compressed pages)
        snapshot = [p.clone() for p in self.pages]
        self.undo_stack.append(snapshot)

    def undo(self):
        if self.undo_stack:
            self.pages = self.undo_stack.pop()
            
            # Find active page
            self.active_page_index = -1
            for i, p in enumerate(self.pages):
                if not p.is_compressed:
                    self.active_page_index = i
            
            if self.active_page_index == -1 and self.pages:
                self.active_page_index = len(self.pages) - 1
                self.pages[self.active_page_index].decompress()

            self.update_widget_size()
            self.update()
            gc.collect()
        else:
            print("Nothing to undo")

    def reset_to_a4(self):
        self.undo_stack.clear()
        gc.collect()
        self.pages = [Page()]
        self.active_page_index = 0
        self.update_widget_size()
        self.update()

    def add_page(self):
        self.save_state()
        
        # Compress old active page
        if 0 <= self.active_page_index < len(self.pages):
            self.pages[self.active_page_index].compress()
        
        self.pages.append(Page())
        self.active_page_index = len(self.pages) - 1
        
        self.update_widget_size()
        self.update()


        self.auto_save()

    def set_pen_color(self, color):
        self.paste_floating_selection()
        self.current_tool = 'pen'
        self.brush_color = color
        self.setCursor(Qt.ArrowCursor)

    def set_eraser(self):
        self.paste_floating_selection()
        self.current_tool = 'eraser'
        self.setCursor(Qt.ArrowCursor)

    def set_move_tool(self):
        self.paste_floating_selection()
        self.current_tool = 'select_box'
        self.setCursor(Qt.CrossCursor)

    def paste_floating_selection(self):
        if self.floating_pixmap:
            center_y = self.floating_pos_img.y() + (self.floating_pixmap.height() // 2)
            page_idx, local_y = self.get_page_at(center_y)
            
            if page_idx != self.active_page_index:
                if 0 <= self.active_page_index < len(self.pages):
                    self.pages[self.active_page_index].compress()
                self.pages[page_idx].decompress()
                self.active_page_index = page_idx
            
            page = self.pages[page_idx]
            painter = QPainter(page.high_res_pixmap)
            draw_pos = QPoint(self.floating_pos_img.x(), self.floating_pos_img.y() - (page_idx * IMG_HEIGHT))
            painter.drawPixmap(draw_pos, self.floating_pixmap)
            painter.end()
            
            self.floating_pixmap = None
            self.update()

    def paste_external_image(self, pixmap):
        self.paste_floating_selection()
        self.save_state()
        
        scaled_pixmap = pixmap.scaled(pixmap.width() * 2, pixmap.height() * 2, 
                                      Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.floating_pixmap = scaled_pixmap
        self.current_tool = 'moving_selection'
        
        last_page_y = (len(self.pages) - 1) * IMG_HEIGHT
        self.floating_pos_img = QPoint(100, last_page_y + 100) 
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            global_pos = self.to_image_coords(event.pos())
            
            # Check for page switch
            page_idx, _ = self.get_page_at(global_pos.y())
            if page_idx != self.active_page_index:
                if 0 <= self.active_page_index < len(self.pages):
                    self.pages[self.active_page_index].compress()
                self.pages[page_idx].decompress()
                self.active_page_index = page_idx
                self.update()

            if self.current_tool == 'select_box':
                self.is_selecting = True
                self.select_start_img = global_pos
                self.select_current_img = global_pos

            elif self.current_tool == 'moving_selection':
                self.paste_floating_selection()
                self.current_tool = 'select_box' 

            elif self.current_tool in ['pen', 'eraser']:
                self.save_state()
                self.drawing = True
                self.last_point_img = global_pos

    def mouseMoveEvent(self, event):
        global_pos = self.to_image_coords(event.pos())

        if self.current_tool == 'moving_selection' and self.floating_pixmap:
            offset_x = self.floating_pixmap.width() // 2
            offset_y = self.floating_pixmap.height() // 2
            self.floating_pos_img = global_pos - QPoint(offset_x, offset_y)
            self.update()
            return

        if (event.buttons() & Qt.LeftButton):
            if self.current_tool == 'select_box' and self.is_selecting:
                self.select_current_img = global_pos
                self.update() 

            elif self.current_tool in ['pen', 'eraser'] and self.drawing:
                page_idx, local_y = self.get_page_at(global_pos.y())
                page = self.pages[page_idx]
                
                if page_idx == self.active_page_index:
                    prev_page_idx, prev_local_y = self.get_page_at(self.last_point_img.y())
                    
                    if prev_page_idx == page_idx:
                        painter = QPainter(page.high_res_pixmap)
                        
                        if self.current_tool == 'eraser':
                            width = self.brush_size * 5
                            pen = QPen(Qt.white, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                        else:
                            pen = QPen(self.brush_color, self.brush_size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                        
                        painter.setPen(pen)
                        start_pt = QPoint(self.last_point_img.x(), prev_local_y)
                        end_pt = QPoint(global_pos.x(), local_y)
                        painter.drawLine(start_pt, end_pt)
                        painter.end()
                
                self.last_point_img = global_pos
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            global_pos = self.to_image_coords(event.pos())

            if self.current_tool == 'select_box' and self.is_selecting:
                self.is_selecting = False
                rect = QRect(self.select_start_img, self.select_current_img).normalized()
                
                if rect.width() > 10 and rect.height() > 10:
                    page_idx, local_y_start = self.get_page_at(rect.top())
                    
                    if page_idx != self.active_page_index:
                         if 0 <= self.active_page_index < len(self.pages):
                            self.pages[self.active_page_index].compress()
                         self.pages[page_idx].decompress()
                         self.active_page_index = page_idx
                    
                    page = self.pages[page_idx]
                    local_rect = QRect(rect.x(), local_y_start, rect.width(), rect.height())
                    local_rect = local_rect.intersected(QRect(0, 0, IMG_WIDTH, IMG_HEIGHT))
                    
                    if not local_rect.isEmpty():
                        self.save_state()
                        self.floating_pixmap = page.high_res_pixmap.copy(local_rect)
                        
                        painter = QPainter(page.high_res_pixmap)
                        painter.fillRect(local_rect, Qt.white)
                        painter.end()
                        
                        self.current_tool = 'moving_selection'
                        offset_x = self.floating_pixmap.width() // 2
                        offset_y = self.floating_pixmap.height() // 2
                        self.floating_pos_img = global_pos - QPoint(offset_x, offset_y)
                        self.update()

            elif self.current_tool in ['pen', 'eraser']:
                self.drawing = False
                if 0 <= self.active_page_index < len(self.pages):
                     self.pages[self.active_page_index].preview_pixmap = self.pages[self.active_page_index].high_res_pixmap.scaled(
                         VIEW_WIDTH, VIEW_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation
                     )

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = event.rect()
        start_page = rect.top() // VIEW_HEIGHT
        end_page = rect.bottom() // VIEW_HEIGHT
        
        if start_page < 0: start_page = 0
        if end_page >= len(self.pages): end_page = len(self.pages) - 1
        
        for i in range(start_page, end_page + 1):
            page = self.pages[i]
            y_pos = i * VIEW_HEIGHT
            
            # 1. Draw Page Content
            if page.is_compressed:
                painter.drawPixmap(0, y_pos, page.preview_pixmap)
            else:
                target_rect = QRect(0, y_pos, VIEW_WIDTH, VIEW_HEIGHT)
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                painter.drawPixmap(target_rect, page.high_res_pixmap, page.high_res_pixmap.rect())

            # 2. Draw Dashed Separator (At the TOP of the page, unless it's the first page)
            if i > 0:
                pen_dash = QPen(Qt.gray, 2, Qt.DashLine)
                painter.setPen(pen_dash)
                painter.drawLine(0, y_pos, VIEW_WIDTH, y_pos)

        # 3. Draw Tools Overlay
        if self.current_tool == 'select_box' and self.is_selecting:
            pen_sel = QPen(Qt.blue, 2, Qt.DashLine)
            painter.setPen(pen_sel)
            painter.setBrush(Qt.NoBrush)
            
            screen_start = self.select_start_img / VIEW_SCALE
            screen_curr = self.select_current_img / VIEW_SCALE
            rect = QRect(screen_start, screen_curr).normalized()
            painter.drawRect(rect)

        if self.current_tool == 'moving_selection' and self.floating_pixmap:
            screen_pos = self.floating_pos_img / VIEW_SCALE
            display_float = self.floating_pixmap.scaled(
                self.floating_pixmap.width() // VIEW_SCALE,
                self.floating_pixmap.height() // VIEW_SCALE,
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(screen_pos, display_float)
            painter.setPen(QPen(Qt.blue, 1, Qt.DotLine))
            painter.drawRect(screen_pos.x(), screen_pos.y(), 
                             display_float.width(), display_float.height())

    def import_pdf(self):
        if not PDF_IMPORT_AVAILABLE:
            QMessageBox.critical(self, "Error", "pdf2image required.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", "PDF Files (*.pdf)")
        if not filename:
            return
        progress = QProgressDialog("Importing PDF...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()
        try:
            pages = convert_from_path(filename, dpi=200) 
            self.save_state()
            
            if 0 <= self.active_page_index < len(self.pages):
                self.pages[self.active_page_index].compress()

            for i, page_pil in enumerate(pages):
                page_pil = page_pil.convert("RGBA")
                data = page_pil.tobytes("raw", "RGBA")
                qimg = QImage(data, page_pil.size[0], page_pil.size[1], QImage.Format_RGBA8888)
                scaled_qimg = qimg.scaledToWidth(IMG_WIDTH, Qt.SmoothTransformation)            

                final_page_pix = QPixmap(IMG_WIDTH, IMG_HEIGHT)
                final_page_pix.fill(Qt.white)
                p = QPainter(final_page_pix)
                p.drawPixmap(0, 0, QPixmap.fromImage(scaled_qimg))
                p.end()
                
                p_obj = Page(final_page_pix)
                p_obj.compress() # Import as compressed
                self.pages.append(p_obj)
            
            self.active_page_index = len(self.pages) - 1
            self.pages[self.active_page_index].decompress()
            
            self.update_widget_size()
            self.update()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed: {str(e)}")
        finally:
            progress.close()
            gc.collect()

    def auto_save(self):
        filename, _ = (os.getcwd()+'/back_up.pdf', 'PDF Files (*.pdf)')
        if not filename: return
        
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(filename)
        printer.setPageSize(QPrinter.A4)
        
        painter = QPainter(printer)
        printer_rect = printer.pageRect()
        scale_x = printer_rect.width() / IMG_WIDTH
        scale_y = printer_rect.height() / IMG_HEIGHT
        painter.scale(scale_x, scale_y)
        
        for i, page in enumerate(self.pages):
            if i > 0: printer.newPage()
            
            was_compressed = page.is_compressed
            if was_compressed: page.decompress()
            
            painter.drawPixmap(0, 0, page.high_res_pixmap)
            
            if was_compressed: page.compress()
                
        painter.end()
        gc.collect()

    def save_pdf_high_res(self):
        default_name = datetime.now().strftime("%Y-%m-%d") + ".pdf"
        filename, _ = QFileDialog.getSaveFileName(self, "Save PDF", default_name, "PDF Files (*.pdf)")
        if not filename: return
        
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(filename)
        printer.setPageSize(QPrinter.A4)
        
        painter = QPainter(printer)
        printer_rect = printer.pageRect()
        scale_x = printer_rect.width() / IMG_WIDTH
        scale_y = printer_rect.height() / IMG_HEIGHT
        painter.scale(scale_x, scale_y)
        
        for i, page in enumerate(self.pages):
            if i > 0: printer.newPage()
            
            was_compressed = page.is_compressed
            if was_compressed: page.decompress()
            
            painter.drawPixmap(0, 0, page.high_res_pixmap)
            
            if was_compressed: page.compress()
                
        painter.end()
        QMessageBox.information(self, "Success", "Saved!")
        gc.collect()

class NotepadApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scrap Paper")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, 'app_icon.png')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setGeometry(100, 100, 1200, 900)
        self.pinned = False
        self.canvas = Canvas()
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)
        
        sidebar = QVBoxLayout()
        sidebar.setAlignment(Qt.AlignTop)
        
        sidebar.addWidget(QLabel("<b>Tools</b>"))
        btn_undo = QPushButton("↶ Undo")
        btn_undo.setStyleSheet("background-color: #ffdddd;")
        btn_undo.clicked.connect(self.canvas.undo)
        sidebar.addWidget(btn_undo)
        
        sidebar.addWidget(QLabel("Thickness:"))
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(2)
        slider.setMaximum(60)
        slider.setValue(8)
        slider.valueChanged.connect(self.canvas.set_brush_size)
        sidebar.addWidget(slider)

        btn_black = QPushButton("Black Pen")
        btn_black.clicked.connect(lambda: self.canvas.set_pen_color(Qt.black))
        sidebar.addWidget(btn_black)

        btn_blue = QPushButton("Blue Pen")
        btn_blue.setStyleSheet("color: blue;")
        btn_blue.clicked.connect(lambda: self.canvas.set_pen_color(Qt.blue))
        sidebar.addWidget(btn_blue)

        btn_red = QPushButton("Red Pen")
        btn_red.setStyleSheet("color: red;")
        btn_red.clicked.connect(lambda: self.canvas.set_pen_color(Qt.red))
        sidebar.addWidget(btn_red)

        btn_color = QPushButton("Pick Color...")
        btn_color.clicked.connect(self.choose_color)
        sidebar.addWidget(btn_color)

        btn_eraser = QPushButton("Eraser")
        btn_eraser.clicked.connect(self.canvas.set_eraser)
        sidebar.addWidget(btn_eraser)

        btn_move = QPushButton("✂ Cut & Move")
        btn_move.setStyleSheet("background-color: #e0e0e0;")
        btn_move.clicked.connect(self.canvas.set_move_tool)
        sidebar.addWidget(btn_move)

        self.add_separator(sidebar)

        sidebar.addWidget(QLabel("<b>Input</b>"))
        btn_add_page = QPushButton("+ Add A4 Page")
        btn_add_page.clicked.connect(self.canvas.add_page)
        sidebar.addWidget(btn_add_page)
        
        btn_grab = QPushButton("📷 Screen Grab")
        btn_grab.clicked.connect(self.start_screen_grab)
        sidebar.addWidget(btn_grab)
        
        btn_import = QPushButton("Import PDF")
        btn_import.clicked.connect(self.canvas.import_pdf)
        sidebar.addWidget(btn_import)
        
        btn_pdf = QPushButton("Save PDF")
        btn_pdf.clicked.connect(self.canvas.save_pdf_high_res)
        sidebar.addWidget(btn_pdf)

        btn_clear = QPushButton("Reset / Clear")
        btn_clear.clicked.connect(self.canvas.reset_to_a4)
        sidebar.addWidget(btn_clear)

        self.add_separator(sidebar)

        sidebar.addWidget(QLabel("<b>System</b>"))
        
        btn_gc = QPushButton("⚡ Compact RAM")
        btn_gc.clicked.connect(lambda: self.canvas.force_gc())
        btn_gc.setToolTip("Force clean unused memory")
        sidebar.addWidget(btn_gc)

        self.script_disable_path = "./disable_tablet_mode.sh"
        self.script_enable_path = "./enable_tablet_mode.sh"
        
        btn_disable_tab = QPushButton("Disable Tablet Mode")
        btn_disable_tab.clicked.connect(lambda: self.run_script(self.script_disable_path))
        sidebar.addWidget(btn_disable_tab)

        btn_enable_tab = QPushButton("Enable Tablet Mode")
        btn_enable_tab.clicked.connect(lambda: self.run_script(self.script_enable_path))
        sidebar.addWidget(btn_enable_tab)
        
        self.btn_pin = QPushButton("Pin on Top: OFF")
        self.btn_pin.setCheckable(True)
        self.btn_pin.clicked.connect(self.toggle_pin)
        sidebar.addWidget(self.btn_pin)

        frame_sidebar = QFrame()
        frame_sidebar.setLayout(sidebar)
        frame_sidebar.setFixedWidth(170)
        layout.addWidget(frame_sidebar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setBackgroundRole(QPalette.Dark)
        self.scroll_area.setStyleSheet("background-color: #ccc;") 
        self.scroll_area.setWidget(self.canvas)
        self.scroll_area.setWidgetResizable(True) 
        self.scroll_area.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.scroll_area)

    def should_close(self):
        reply = QMessageBox.question(self, 'Confirmation', 'Do you want to close?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            return True
        else:
            return False

    def closeEvent(self, event):
        if self.should_close():
            self.on_close()
            event.accept()
        else:
            event.ignore()

    def on_close(self):
        #We can add here an emergency save?
        print("Window is closing — protocol executed")


    def add_separator(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

    def choose_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.canvas.set_pen_color(color)

    def toggle_pin(self):
        self.pinned = not self.pinned
        if self.pinned:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            self.btn_pin.setText("Pin on Top: ON")
            self.btn_pin.setStyleSheet("background-color: #aaffaa")
            self.show()
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
            self.btn_pin.setText("Pin on Top: OFF")
            self.btn_pin.setStyleSheet("")
            self.show()

    def run_script(self, script_path):
        if not os.path.exists(script_path):
            QMessageBox.warning(self, "Error", f"Script not found at:\n{script_path}")
            return
        try:
            subprocess.Popen(['/bin/bash', script_path])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to run script:\n{str(e)}")
    
    def start_screen_grab(self):
        self.hide()
        self.snipper = SnippingTool()
        self.snipper.snippet_captured.connect(self.finish_screen_grab)
        self.snipper.show()
        
    def finish_screen_grab(self, pixmap):
        try:
            self.showNormal() 
            if self.pinned:
                self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
                self.show()
            self.canvas.paste_external_image(pixmap)
        except Exception as e:
            print(e)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = NotepadApp()
    window.show()
    sys.exit(app.exec_())