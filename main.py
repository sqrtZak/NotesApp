import sys
import subprocess
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QScrollArea, QColorDialog, 
                             QLabel, QMessageBox, QFrame, QFileDialog, QProgressDialog)
from PyQt5.QtGui import QPainter, QPen, QPixmap, QPalette, QColor, QCursor, QIcon, QImage
from PyQt5.QtCore import Qt, QPoint, QRect, QSize, pyqtSignal
from PyQt5.QtPrintSupport import QPrinter

# Try importing pdf2image for PDF importing features
try:
    from pdf2image import convert_from_path
    PDF_IMPORT_AVAILABLE = True
except ImportError:
    PDF_IMPORT_AVAILABLE = False

# Constants
A4_WIDTH = 794
A4_HEIGHT = 1123
UNDO_LIMIT = 5

class SnippingTool(QWidget):
    """
    A fullscreen overlay that lets the user select a region of the screen.
    Emits the 'snippet_captured' signal with the cropped QPixmap.
    """
    snippet_captured = pyqtSignal(QPixmap)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setWindowState(Qt.WindowFullScreen)
        self.setCursor(Qt.CrossCursor)
        
        # Capture the entire screen immediately
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
        # 1. Draw the full screenshot
        painter.drawPixmap(0, 0, self.original_pixmap)
        
        # 2. Draw a semi-transparent black overlay to "dim" the screen
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.setPen(Qt.NoPen)
        painter.drawRect(self.rect())
        
        # 3. Clear the dimming from the selected area (highlight it)
        if self.is_selecting:
            rect = QRect(self.start_point, self.end_point).normalized()
            painter.drawPixmap(rect, self.original_pixmap, rect)
            
            # Draw a red border around selection
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(Qt.red, 2))
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
            
            # Calculate rect
            rect = QRect(self.start_point, self.end_point).normalized()
            
            # Ensure valid size
            if rect.width() > 5 and rect.height() > 5:
                cropped = self.original_pixmap.copy(rect)
                self.snippet_captured.emit(cropped)
                self.close()
            else:
                # If click was too small, just cancel/close
                self.close()

    def keyPressEvent(self, event):
        # Allow cancelling with Escape
        if event.key() == Qt.Key_Escape:
            self.close()

class Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StaticContents)
        self.setMouseTracking(True)
        
        self.image = QPixmap(A4_WIDTH, A4_HEIGHT)
        self.image.fill(Qt.white)
        
        self.undo_stack = []
        
        self.drawing = False
        self.last_point = QPoint()
        
        # Tools: 'pen', 'eraser', 'select_box', 'moving_selection'
        self.current_tool = 'pen' 
        self.brush_size = 3
        self.brush_color = Qt.black

        self.select_start = QPoint()
        self.select_current = QPoint()
        self.is_selecting = False       
        self.floating_pixmap = None     
        self.floating_pos = QPoint()    

        self.setFixedSize(self.image.size())

    def save_state(self):
        if len(self.undo_stack) >= UNDO_LIMIT:
            self.undo_stack.pop(0) 
        self.undo_stack.append(self.image.copy())

    def undo(self):
        if self.undo_stack:
            self.image = self.undo_stack.pop()
            self.setFixedSize(self.image.size())
            self.update()
        else:
            print("Nothing to undo")

    def reset_to_a4(self):
        self.save_state()
        self.image = QPixmap(A4_WIDTH, A4_HEIGHT)
        self.image.fill(Qt.white)
        self.setFixedSize(self.image.size())
        self.update()

    def add_page(self):
        self.save_state()
        current_width = self.image.width()
        current_height = self.image.height()
        new_height = current_height + A4_HEIGHT
        
        new_image = QPixmap(current_width, new_height)
        new_image.fill(Qt.white)
        
        painter = QPainter(new_image)
        painter.drawPixmap(0, 0, self.image)
        painter.end()
        
        self.image = new_image
        self.setFixedSize(self.image.size())
        self.update()

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
            painter = QPainter(self.image)
            painter.drawPixmap(self.floating_pos, self.floating_pixmap)
            painter.end()
            self.floating_pixmap = None
            self.update()

    def paste_external_image(self, pixmap):
        """Receives a pixmap (e.g. from screenshot) and starts moving it."""
        self.paste_floating_selection() # Commit anything currently floating
        self.save_state()
        
        self.floating_pixmap = pixmap
        self.current_tool = 'moving_selection'
        
        # Default position: Top left or center of view? 
        # We'll rely on mouseMove to snap it to cursor immediately
        self.floating_pos = QPoint(50, 50) 
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            
            if self.current_tool == 'select_box':
                self.is_selecting = True
                self.select_start = event.pos()
                self.select_current = event.pos()

            elif self.current_tool == 'moving_selection':
                self.paste_floating_selection()
                self.current_tool = 'select_box' 

            elif self.current_tool in ['pen', 'eraser']:
                self.save_state()
                self.drawing = True
                self.last_point = event.pos()

    def mouseMoveEvent(self, event):
        if self.current_tool == 'moving_selection' and self.floating_pixmap:
            offset_x = self.floating_pixmap.width() // 2
            offset_y = self.floating_pixmap.height() // 2
            self.floating_pos = event.pos() - QPoint(offset_x, offset_y)
            self.update()
            return

        if (event.buttons() & Qt.LeftButton):
            if self.current_tool == 'select_box' and self.is_selecting:
                self.select_current = event.pos()
                self.update() 

            elif self.current_tool in ['pen', 'eraser'] and self.drawing:
                painter = QPainter(self.image)
                if self.current_tool == 'eraser':
                    pen = QPen(Qt.white, 20, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                else:
                    pen = QPen(self.brush_color, self.brush_size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen)
                painter.drawLine(self.last_point, event.pos())
                painter.end()
                self.last_point = event.pos()
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.current_tool == 'select_box' and self.is_selecting:
                self.is_selecting = False
                rect = QRect(self.select_start, self.select_current).normalized()
                
                if rect.width() > 5 and rect.height() > 5:
                    self.save_state()
                    self.floating_pixmap = self.image.copy(rect)
                    
                    painter = QPainter(self.image)
                    painter.fillRect(rect, Qt.white)
                    painter.end()
                    
                    self.current_tool = 'moving_selection'
                    offset_x = self.floating_pixmap.width() // 2
                    offset_y = self.floating_pixmap.height() // 2
                    self.floating_pos = event.pos() - QPoint(offset_x, offset_y)
                    self.update()

            elif self.current_tool in ['pen', 'eraser']:
                self.drawing = False

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = event.rect()
        painter.drawPixmap(rect, self.image, rect)
        
        pen_dash = QPen(Qt.gray, 2, Qt.DashLine)
        painter.setPen(pen_dash)
        total_height = self.image.height()
        y = A4_HEIGHT
        while y < total_height:
            painter.drawLine(0, y, self.image.width(), y)
            y += A4_HEIGHT

        if self.current_tool == 'select_box' and self.is_selecting:
            pen_sel = QPen(Qt.blue, 2, Qt.DashLine)
            painter.setPen(pen_sel)
            painter.setBrush(Qt.NoBrush)
            rect = QRect(self.select_start, self.select_current).normalized()
            painter.drawRect(rect)

        if self.current_tool == 'moving_selection' and self.floating_pixmap:
            painter.drawPixmap(self.floating_pos, self.floating_pixmap)
            painter.setPen(QPen(Qt.blue, 1, Qt.DotLine))
            painter.drawRect(self.floating_pos.x(), self.floating_pos.y(), 
                             self.floating_pixmap.width(), self.floating_pixmap.height())

    def import_pdf(self):
        if not PDF_IMPORT_AVAILABLE:
            QMessageBox.critical(self, "Error", "pdf2image library not found.\nPlease install: pip install pdf2image\nAnd: sudo apt install poppler-utils")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", "PDF Files (*.pdf)")
        if not filename:
            return
        progress = QProgressDialog("Importing PDF...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()
        try:
            pages = convert_from_path(filename, dpi=100) 
            self.save_state()
            total_pages = len(pages)
            start_y = self.image.height()
            
            if start_y == A4_HEIGHT and self.image.toImage().pixel(A4_WIDTH//2, A4_HEIGHT//2) == 4294967295:
                start_y = 0
                new_total_h = A4_HEIGHT * total_pages
            else:
                new_total_h = start_y + (A4_HEIGHT * total_pages)

            if new_total_h > self.image.height():
                new_image = QPixmap(A4_WIDTH, new_total_h)
                new_image.fill(Qt.white)
                painter = QPainter(new_image)
                painter.drawPixmap(0, 0, self.image)
            else:
                new_image = self.image
                painter = QPainter(new_image)

            for i, page_pil in enumerate(pages):
                page_pil = page_pil.convert("RGBA")
                data = page_pil.tobytes("raw", "RGBA")
                qimg = QImage(data, page_pil.size[0], page_pil.size[1], QImage.Format_RGBA8888)
                scaled_qimg = qimg.scaledToWidth(A4_WIDTH, Qt.SmoothTransformation)
                target_y = start_y + (i * A4_HEIGHT)
                painter.drawImage(0, target_y, scaled_qimg)
                
            painter.end()
            self.image = new_image
            self.setFixedSize(self.image.size())
            self.update()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import PDF: {str(e)}")
        finally:
            progress.close()

    def save_pdf_high_res(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save PDF", "notes.pdf", "PDF Files (*.pdf)")
        if not filename:
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(filename)
        printer.setPageSize(QPrinter.A4)
        painter = QPainter(printer)
        printer_rect = printer.pageRect()
        scale_x = printer_rect.width() / self.image.width()
        scale_y = printer_rect.height() / A4_HEIGHT
        painter.scale(scale_x, scale_y)
        current_y = 0
        total_height = self.image.height()
        while current_y < total_height:
            source_rect = QRect(0, current_y, self.image.width(), A4_HEIGHT)
            painter.drawPixmap(0, 0, self.image, source_rect.x(), source_rect.y(), source_rect.width(), source_rect.height())
            current_y += A4_HEIGHT
            if current_y < total_height:
                printer.newPage()
        painter.end()
        QMessageBox.information(self, "Success", "High-Resolution PDF Saved!")

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
        
        # Sidebar
        sidebar = QVBoxLayout()
        sidebar.setAlignment(Qt.AlignTop)
        
        sidebar.addWidget(QLabel("<b>Tools</b>"))
        
        btn_undo = QPushButton("↶ Undo")
        btn_undo.setStyleSheet("background-color: #ffdddd;")
        btn_undo.clicked.connect(self.canvas.undo)
        sidebar.addWidget(btn_undo)
        
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
        btn_move.setToolTip("Drag a box to cut, click again to paste")
        btn_move.setStyleSheet("background-color: #e0e0e0;")
        btn_move.clicked.connect(self.canvas.set_move_tool)
        sidebar.addWidget(btn_move)

        self.add_separator(sidebar)

        sidebar.addWidget(QLabel("<b>Input</b>"))
        btn_add_page = QPushButton("+ Add A4 Page")
        btn_add_page.clicked.connect(self.canvas.add_page)
        sidebar.addWidget(btn_add_page)
        
        # --- NEW BUTTON: Screen Grab ---
        btn_grab = QPushButton("Screen Grab")
        btn_grab.setToolTip("Hide app and select screen area to copy")
        btn_grab.clicked.connect(self.start_screen_grab)
        sidebar.addWidget(btn_grab)
        
        btn_import = QPushButton("Import PDF")
        btn_import.clicked.connect(self.canvas.import_pdf)
        sidebar.addWidget(btn_import)
        
        btn_pdf = QPushButton("Save PDF (Hi-Res)")
        btn_pdf.clicked.connect(self.canvas.save_pdf_high_res)
        sidebar.addWidget(btn_pdf)

        btn_clear = QPushButton("Reset / Clear")
        btn_clear.clicked.connect(self.canvas.reset_to_a4)
        sidebar.addWidget(btn_clear)

        self.add_separator(sidebar)

        sidebar.addWidget(QLabel("<b>System</b>"))
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
            
    # --- NEW METHOD: Handle Screen Grab ---
    def start_screen_grab(self):
        # 1. Hide the main app so it doesn't get in the way
        self.hide()
        
        # 2. Wait a split second to ensure window animation clears? 
        # Actually, since we run SnippingTool on main thread, immediate hide works usually.
        
        # 3. Launch Snipping Tool
        self.snipper = SnippingTool()
        self.snipper.snippet_captured.connect(self.finish_screen_grab)
        self.snipper.show()
        
    def finish_screen_grab(self, pixmap):
        # 1. Show the app again
        self.showNormal() 
        if self.pinned:
            # Re-apply pin if it was on
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            self.show()
            
        # 2. Pass image to canvas
        self.canvas.paste_external_image(pixmap)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = NotepadApp()
    window.show()
    sys.exit(app.exec_())