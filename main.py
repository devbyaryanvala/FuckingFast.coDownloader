import os
import re
import sys
import time
import webbrowser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import json # For persistent theme settings

import requests
from bs4 import BeautifulSoup

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QUrl, QThread, pyqtSignal, QMutex, QMutexLocker, QPropertyAnimation, QEasingCurve, QRect, QSize
from PyQt5.QtGui import QFont, QFontDatabase, QDesktopServices, QColor, QPalette, QPixmap, QPainter, QLinearGradient # Import QFontDatabase
from qt_material import apply_stylesheet

try:
    import qtawesome as qta
except ImportError:
    qta = None
    print("qt_awesome not installed. Icons will not be displayed.")


# Global configuration
INPUT_FILE = "input.txt"
DOWNLOADS_FOLDER = "downloads"
MAX_WORKERS = 6  # Maximum concurrent download chunks
CONFIG_FILE = "config.json" # For persistent settings

if not os.path.exists(DOWNLOADS_FOLDER):
    os.makedirs(DOWNLOADS_FOLDER)

HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'accept-language': 'en-US,en;q=0.5',
    'referer': 'https://fitgirl-repacks.site/',
    'sec-ch-ua': '"Brave";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'user-agent': (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

# ---------------------------------------------------------------------------
# Helper function to colorize log messages based on content.
def colorize_log_message(message):
    """
    Return the message wrapped in an HTML span with a color and emoji
    based on keywords in the message.
    """
    msg_lower = message.lower()
    emoji = ""
    
    if "error" in msg_lower or "❌" in message:
        color = "#FF6347"  # Tomato
        if "❌" not in message:
            emoji = "❌ "
    elif "completed" in msg_lower or "✅" in message:
        color = "#32CD32"  # LimeGreen
        if "✅" not in message:
            emoji = "✅ "
    elif "paused" in msg_lower or "⏸️" in message:
        color = "#FFD700"  # Gold
        if "⏸️" not in message:
            emoji = "⏸️ "
    elif "resumed" in msg_lower or "▶️" in message:
        color = "#00BFFF"  # DeepSkyBlue
        if "▶️" not in message:
            emoji = "▶️ "
    elif "downloading" in msg_lower or "⬇️" in message:
        color = "#1E90FF"  # DodgerBlue
        if "⬇️" not in message:
            emoji = "⬇️ "
    elif "processing link" in msg_lower or "fetching" in msg_lower or "🔗" in message:
        color = "#40E0D0"  # Turquoise
        if "🔗" not in message:
            emoji = "🔗 "
    elif "loaded" in msg_lower or "imported" in msg_lower or "📥" in message:
        color = "#DA70D6"  # Orchid
        if "📥" not in message:
            emoji = "📥 "
    elif "removed" in msg_lower or "deleted" in msg_lower or "🗑️" in message:
        color = "#A9A9A9" # DarkGray
        if "🗑️" not in message:
            emoji = "🗑️ "
    elif "stopping" in msg_lower or "stopped" in msg_lower or "🛑" in message:
        color = "#FF4500" # OrangeRed
        if "🛑" not in message:
            emoji = "🛑 "
    else:
        color = "#E0E0E0"  # Default to light grey for general messages

    return f"<span style='color:{color};'>{emoji}{message}</span>"

# Custom animated progress bar
class AnimatedProgressBar(QtWidgets.QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(35)
        
    def paintEvent(self, event):
        super().paintEvent(event)
        
        # Add subtle glow effect when downloading
        if self.value() > 0 and self.value() < self.maximum():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # Create glow effect
            glow_rect = self.rect().adjusted(1, 1, -1, -1)
            gradient = QLinearGradient(0, 0, glow_rect.width(), 0)
            gradient.setColorAt(0, QColor(64, 224, 208, 30))
            gradient.setColorAt(0.5, QColor(64, 224, 208, 80))
            gradient.setColorAt(1, QColor(64, 224, 208, 30))
            
            painter.setBrush(gradient)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(glow_rect, 8, 8)

# Custom status indicator with animations
class StatusIndicator(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(50)
        self._status_color = QColor("#27AE60")
        
        # Animation for pulsing effect
        self.pulse_animation = QPropertyAnimation(self, b"status_color")
        self.pulse_animation.setDuration(1500)
        self.pulse_animation.setLoopCount(-1)
        self.pulse_animation.valueChanged.connect(self.update)
        
    def get_status_color(self):
        return self._status_color
        
    def set_status_color(self, color):
        self._status_color = color
        self.update()
        
    status_color = QtCore.pyqtProperty(QColor, get_status_color, set_status_color)
    
    def set_status(self, status_text, color_name="green"):
        self.setText(status_text)
        
        color_map = {
            "green": QColor("#27AE60"),
            "blue": QColor("#1E90FF"), 
            "gold": QColor("#FFD700"),
            "red": QColor("#FF6347")
        }
        
        base_color = color_map.get(color_name, QColor("#27AE60"))
        self._status_color = base_color
        
        # Start pulsing animation for active states
        if color_name in ["blue", "gold"]:  # downloading or paused
            darker = base_color.darker(120)
            self.pulse_animation.setStartValue(base_color)
            self.pulse_animation.setEndValue(darker)
            self.pulse_animation.start()
        else:
            self.pulse_animation.stop()
            
        self.setStyleSheet(f"""
            StatusIndicator {{
                background-color: {base_color.name()};
                border: 2px solid {base_color.lighter(120).name()};
                border-radius: 15px;
                padding: 10px;
                font-weight: bold;
                font-size: 14px;
                color: white;
            }}
        """)

# Enhanced responsive splitter
class ResponsiveSplitter(QtWidgets.QSplitter):
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(12)
        self.setChildrenCollapsible(False)
        
        # Custom splitter handle styling
        self.setStyleSheet("""
            QSplitter::handle {
                background-color: #353B48;
                border: 1px solid #40E0D0;
                border-radius: 6px;
                margin: 2px;
            }
            QSplitter::handle:hover {
                background-color: #40E0D0;
            }
            QSplitter::handle:pressed {
                background-color: #5DADE2;
            }
        """)

# Responsive scroll area for adaptive layouts
class ResponsiveScrollArea(QtWidgets.QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Enhanced scrollbar styling
        self.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                border: none;
                background-color: rgba(64, 224, 208, 0.1);
                border-radius: 6px;
            }
            QScrollBar:vertical {
                width: 12px;
                margin: 3px;
            }
            QScrollBar:horizontal {
                height: 12px;
                margin: 3px;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background-color: #40E0D0;
                border-radius: 5px;
                min-height: 20px;
                min-width: 20px;
            }
            QScrollBar::handle:hover {
                background-color: #5DADE2;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
            }
        """)

# Enhanced list widget with better drag/drop feedback and responsive behavior
class QListWidgetLinks(QtWidgets.QListWidget):
    """
    A QListWidget subclass that enables drag-and-drop for URLs
    and provides a context menu for list items with responsive behavior.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setToolTip("Drag and drop links here, or double-click to copy. Right-click for more options.")
        
        # Enhanced visual feedback with responsive sizing
        self.setAlternatingRowColors(True)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setUniformItemSizes(False)  # Allow dynamic item sizing
        
        # Drag feedback overlay
        self.drag_overlay = QtWidgets.QLabel(self)
        self.drag_overlay.hide()
        self.drag_overlay.setStyleSheet("""
            QLabel {
                background-color: rgba(64, 224, 208, 100);
                border: 3px dashed #40E0D0;
                border-radius: 15px;
                color: #40E0D0;
                font-size: 16px;
                font-weight: bold;
                text-align: center;
            }
        """)
        self.drag_overlay.setText("Drop links here!")
        self.drag_overlay.setAlignment(Qt.AlignCenter)

    def resizeEvent(self, event: QtGui.QResizeEvent):
        super().resizeEvent(event)
        self.current_width = self.width()
        self.current_height = self.height()

        # Responsive sidebar/content stacking
        if hasattr(self, 'main_splitter'):
            if self.current_width < 700:
                self.main_splitter.setOrientation(Qt.Vertical)
                self.main_splitter.setSizes([180, self.current_height - 180])
            else:
                self.main_splitter.setOrientation(Qt.Horizontal)
                self.main_splitter.setSizes([300, self.current_width - 300])

        # Responsive button text
        if hasattr(self, 'download_btn'):
            if self.current_width < 600:
                self.download_btn.setText("🚀 Download")
                self.add_links_btn.setText("➕ Add")
                self.open_downloads_btn.setText("📁 Files")
            else:
                self.download_btn.setText("🚀 Download All")
                self.add_links_btn.setText("➕ Add Links")
                self.open_downloads_btn.setText("📁 Downloads")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drag_overlay.show()
            self.drag_overlay.raise_()
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.drag_overlay.hide()
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        self.drag_overlay.hide()
        if event.mimeData().hasUrls():
            links_added = 0
            for url in event.mimeData().urls():
                if url.scheme() in ('http', 'https'):
                    item_text = url.toString()
                    # Prevent adding duplicates by checking the link part after numbering
                    if not any(self.item(i).text().split(". ", 1)[-1] == item_text for i in range(self.count())):
                        self.addItem(item_text)
                        # Also add to the main window's download queue
                        self.parent().parent().download_queue.append(item_text)
                        links_added += 1
            if links_added > 0:
                self.parent().parent().log(f"📥 Added {links_added} link(s) via drag & drop.")
                self.parent().parent().update_link_numbers()
                self.parent().parent()._update_input_file()
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def contextMenuEvent(self, event):
        context_menu = QtWidgets.QMenu(self)
        context_menu.setStyleSheet("""
            QMenu {
                background-color: #2B2B2B;
                color: #E0E0E0;
                border: 1px solid #40E0D0;
                border-radius: 5px;
                padding: 5px;
            }
            QMenu::item {
                padding: 8px 20px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #40E0D0;
                color: #23272E;
            }
        """)
        
        copy_action = context_menu.addAction("📋 Copy Link")
        open_action = context_menu.addAction("🌐 Open in Browser")
        remove_action = context_menu.addAction("🗑️ Remove Selected")
        clear_all_action = context_menu.addAction("🧹 Clear All Links")

        action = context_menu.exec_(self.mapToGlobal(event.pos()))

        if action == copy_action:
            if self.currentItem():
                self.parent().parent().copy_link_to_clipboard(self.currentItem())
        elif action == open_action:
            selected_items = self.selectedItems()
            if selected_items:
                link = selected_items[0].text().split(". ", 1)[-1]
                QDesktopServices.openUrl(QUrl(link))
                self.parent().parent().log(f"🌐 Opening link: {link[:50]}...")
        elif action == remove_action:
            self.parent().parent().remove_selected_links()
        elif action == clear_all_action:
            self.parent().parent().clear_all_links()

# Enhanced button with hover animations and responsive sizing
class AnimatedButton(QtWidgets.QPushButton):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        
        # Hover animation
        self.hover_animation = QPropertyAnimation(self, b"geometry")
        self.hover_animation.setDuration(200)
        self.hover_animation.setEasingCurve(QEasingCurve.OutCubic)
        
        self.original_size = None
        
    def sizeHint(self):
        # Provide responsive size hints
        size = super().sizeHint()
        size.setWidth(max(120, size.width()))
        return size
        
    def enterEvent(self, event):
        if not self.original_size:
            self.original_size = self.geometry()
            
        # Subtle scale up on hover
        new_rect = self.original_size.adjusted(-2, -1, 2, 1)
        self.hover_animation.setStartValue(self.geometry())
        self.hover_animation.setEndValue(new_rect)
        self.hover_animation.start()
        
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        if self.original_size:
            self.hover_animation.setStartValue(self.geometry())
            self.hover_animation.setEndValue(self.original_size)
            self.hover_animation.start()
            
        super().leaveEvent(event)

# Responsive layout widget that adapts to different screen sizes
class ResponsiveWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.compact_mode = False
        self.current_width = 0
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        new_width = event.size().width()
        
        # Switch to compact mode for narrow windows
        should_be_compact = new_width < 900
        
        if should_be_compact != self.compact_mode:
            self.compact_mode = should_be_compact
            self.update_layout()
        
        self.current_width = new_width
    
    def update_layout(self):
        # Override in subclasses to implement responsive behavior
        pass

# ----------------------- GUI Code -----------------------
class DownloaderWorker(QThread):
    """
    Thread-safe worker with proper signal handling for downloading files.
    """
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int) # downloaded_bytes, total_bytes
    file_signal = pyqtSignal(str) # current filename
    status_signal = pyqtSignal(str) # overall status text
    speed_signal = pyqtSignal(float, float, float) # current_speed, overall_speed, eta_seconds
    link_completed_signal = pyqtSignal(str) # link that finished successfully
    link_failed_signal = pyqtSignal(str, str) # link, error_message
    session_finished_signal = pyqtSignal(list, list) # completed_links, failed_links
    link_processing_signal = pyqtSignal(str) # Signal for when a link starts processing

    def __init__(self, links, parent=None):
        super().__init__(parent)
        self.links = links
        self._is_paused = False
        self._lock = QMutex()
        self.active = True # Controls the main loop and threads
        self.current_download_url = None # To potentially allow stopping current download
        
        self.completed_links = []
        self.failed_links = []

        # For speed calculation
        self.dl_start_time = 0.0
        self.last_update_time = 0.0
        self.last_downloaded_bytes = 0
        self.total_paused_duration = 0.0
        self.pause_start_time = 0.0
        # Added for less frequent log updates of progress
        self.last_log_time = 0.0 
        self.last_logged_bytes = 0


    def pause(self):
        with QMutexLocker(self._lock):
            self._is_paused = True
            self.pause_start_time = time.time() # Record pause start time
        self.status_signal.emit("Paused")
        self.log_signal.emit("⏸ Download paused.")

    def resume_download(self):
        with QMutexLocker(self._lock):
            if self._is_paused:
                self.total_paused_duration += (time.time() - self.pause_start_time)
            self._is_paused = False
            self.pause_start_time = 0.0 # Reset pause start time
        self.status_signal.emit("Resuming...")
        self.log_signal.emit("▶ Download resumed.")

    def stop(self):
        self.active = False
        # Wake up any sleeping threads if paused
        self.resume_download()  # This will also update total_paused_duration if it was paused
        # Wait for the thread to finish cleanly, with a timeout
        if self.isRunning():
            self.wait(2000) # Wait up to 2 seconds for clean exit
            if self.isRunning(): # If still running, force termination
                self.terminate()
                self.wait(500)
        self.log_signal.emit("🛑 Download worker stopped.")


    def run(self):
        """Main thread entry point with detailed logging"""
        self.log_signal.emit("🚀 Starting download session...")
        self.status_signal.emit("Starting...")
        start_session = time.time()
        
        # Operate on a copy of links for iteration to allow modifications to original self.links
        current_links_to_process = self.links[:]
        
        for idx, link in enumerate(current_links_to_process, 1):
            if not self.active:
                self.log_signal.emit("Session interrupted.")
                break
            
            self.log_signal.emit(
                f"🔗 Processing link {idx}/{len(current_links_to_process)}\n"
                f"    URL: {link[:70]}{'...' if len(link) > 70 else ''}"
            )
            self.status_signal.emit(f"Fetching: {link[:30]}...")
            self.link_processing_signal.emit(link) # Signal that this link is now being processed

            try:
                # Pause mechanism check before processing each link
                while self.should_pause() and self.active:
                    time.sleep(0.1)
                if not self.active: break # Check again after pause

                file_name, download_url = self._process_link(link)
                self.current_download_url = download_url # Store for potential external stop
                output_path = os.path.join(DOWNLOADS_FOLDER, file_name)
                
                remote_size_mb = self._get_remote_size(download_url)
                self.log_signal.emit(
                    f"📁 File identified\n"
                    f"    Name: {file_name}\n"
                    f"    Size: {remote_size_mb:.2f} MB"
                )
                self.file_signal.emit(file_name) # Update UI with current file
                
                self.dl_start_time = time.time() # Reset for each new download
                self.last_update_time = self.dl_start_time
                self.last_downloaded_bytes = 0
                self.total_paused_duration = 0.0
                self.pause_start_time = 0.0
                self.last_log_time = self.dl_start_time # Initialize log timing
                self.last_logged_bytes = 0


                self._download_file(download_url, output_path)
                
                self.log_signal.emit(
                    f"✅ Download completed\n"
                    f"    Time: {time.time() - self.dl_start_time - self.total_paused_duration:.1f}s\n"
                    f"    Path: {output_path}"
                )
                self.link_completed_signal.emit(link) # Signal successful completion
                self.completed_links.append(link)
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Network error: {e}"
                self.log_signal.emit(f"❌ Error for {link[:50]}...: {error_msg}")
                self.link_failed_signal.emit(link, error_msg)
                self.failed_links.append(link)
            except Exception as e:
                error_msg = f"General error: {e}"
                self.log_signal.emit(f"❌ Error for {link[:50]}...: {error_msg}")
                self.link_failed_signal.emit(link, error_msg)
                self.failed_links.append(link)
            finally:
                self.current_download_url = None # Clear current download reference
                self.progress_signal.emit(0, 0) # Reset progress bar for next item

        total_time = time.time() - start_session
        self.log_signal.emit(
            f"🏁 Session finished\n"
            f"    Duration: {total_time:.1f}s\n"
            f"    Processed: {len(current_links_to_process)} files"
        )
        self.status_signal.emit("Idle")
        self.session_finished_signal.emit(self.completed_links, self.failed_links) # Signal that the entire session is done


    def _get_remote_size(self, url):
        """Get file size in megabytes, safely handles missing header"""
        try:
            head = requests.head(url, headers=HEADERS, timeout=10)
            content_length = head.headers.get('content-length')
            if content_length:
                size_bytes = int(content_length)
                return size_bytes / (1024 * 1024)
            return 0.0 # Return 0 if content-length is missing
        except requests.RequestException:
            return 0.0 # Return 0 on network errors

    def _download_file(self, url, path):
        """Download dispatcher with enhanced speed tracking"""
        head = requests.head(url, headers=HEADERS, timeout=10)
        total_size = int(head.headers.get('content-length', 0))
        accept_ranges = 'bytes' in head.headers.get('Accept-Ranges', '')
        
        self.status_signal.emit(f"Downloading: {os.path.basename(path)}")

        if total_size > 0 and total_size > 1024 * 1024 and accept_ranges:  # Multi-thread for >1MB
            self._chunked_download(url, path, total_size)
        else:
            self._single_thread_download(url, path, total_size) # Pass total_size here


    def _chunked_download(self, url, path, total_size):
        """Threaded download with accurate speed updates"""
        chunk_size = 4 * 1024 * 1024  # 4MB chunks
        # Ensure path exists and pre-allocate file size for random access
        try:
            with open(path, 'wb') as f:
                f.truncate(total_size)
        except OSError as e:
            self.log_signal.emit(f"❌ Error pre-allocating file {path}: {e}")
            raise # Re-raise to be caught by the main run loop

        chunks_to_download = []
        for start in range(0, total_size, chunk_size):
            end = min(start + chunk_size - 1, total_size - 1)
            chunks_to_download.append((start, end))

        # Use a list to track downloaded bytes for each chunk
        chunk_downloaded_bytes = [0] * len(chunks_to_download)
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(
                self._download_chunk,
                url, start, end, path, i # Pass index i
            ): i for i, (start, end) in enumerate(chunks_to_download)}

            downloaded = 0
            while downloaded < total_size and self.active:
                # Update progress more frequently than just on chunk completion
                current_downloaded = sum(chunk_downloaded_bytes)
                self._update_speed_metrics(current_downloaded, total_size)
                
                # Check for completed futures
                # Iterate through a copy to allow modification if retrying
                completed_futures = []
                for future in list(futures.keys()): # Iterate over a copy of keys
                    if future.done():
                        completed_futures.append(future)
                        
                for future in completed_futures:
                    if not self.active: break
                    chunk_index = futures.pop(future) # Remove from active futures
                    try:
                        chunk_bytes_downloaded = future.result()
                        chunk_downloaded_bytes[chunk_index] = chunk_bytes_downloaded
                        downloaded = sum(chunk_downloaded_bytes) # Update total downloaded
                    except Exception as e:
                        range_info = chunks_to_download[chunk_index]
                        self.log_signal.emit(f"⚠️ Chunk ({range_info[0]}-{range_info[1]}) failed: {str(e)}")
                        # For robustness, you might re-submit failed chunks here.
                        # For simplicity, we just log and continue, marking it as potentially incomplete if not handled.
                
                if not self.active:
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.log_signal.emit("Download cancelled during chunk processing.")
                    break

                while self.should_pause() and self.active:
                    time.sleep(0.1)
                if not self.active:
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.log_signal.emit("Download cancelled during pause.")
                    break
                
                # Small sleep to prevent busy-waiting if no futures are done yet
                if not completed_futures and self.active:
                    time.sleep(0.05)


            if self.active: # Only if not explicitly stopped
                self._update_speed_metrics(total_size, total_size) # Ensure 100% update

    def _update_speed_metrics(self, downloaded_bytes, total_bytes):
        """Calculate and emit speed/progress updates"""
        now = time.time()
        
        elapsed_time = now - self.dl_start_time - self.total_paused_duration
        
        current_speed_bps = 0
        # Only calculate if enough time has passed to avoid division by zero or jittery values
        if (now - self.last_update_time) > 0.1: 
            current_speed_bps = (downloaded_bytes - self.last_downloaded_bytes) / (now - self.last_update_time)
            self.last_update_time = now
            self.last_downloaded_bytes = downloaded_bytes
        
        overall_speed_bps = downloaded_bytes / elapsed_time if elapsed_time > 0 else 0
        
        remaining_bytes = total_bytes - downloaded_bytes
        eta_seconds = remaining_bytes / overall_speed_bps if overall_speed_bps > 0 else 0

        self.progress_signal.emit(downloaded_bytes, total_bytes)
        self.speed_signal.emit(current_speed_bps, overall_speed_bps, eta_seconds)

        # Log to QTextEdit less frequently for readability
        # Log only if a significant amount downloaded or 5 seconds passed or on completion
        if (downloaded_bytes - self.last_logged_bytes) > (1024 * 1024) * 5 or \
           (now - self.last_log_time) > 5.0 or \
           downloaded_bytes == total_bytes:
            
            self.log_signal.emit(
                f"⬇️ Progress: {self._format_bytes(downloaded_bytes)}/{self._format_bytes(total_bytes)} "
                f"Speed: {self._format_speed(overall_speed_bps)} ETA: {self._format_eta(eta_seconds)}"
            )
            self.last_log_time = now
            self.last_logged_bytes = downloaded_bytes


    def _format_bytes(self, bytes_val):
        """Formats bytes into human-readable KBs, MBs, GBs"""
        if bytes_val < 1024:
            return f"{bytes_val:.2f} B"
        elif bytes_val < 1024 * 1024:
            return f"{bytes_val / 1024:.2f} KB"
        elif bytes_val < 1024 * 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.2f} MB"
        else:
            return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"

    def _format_speed(self, bytes_per_sec):
        """Formats speed into human-readable KB/s, MB/s, GB/s"""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.2f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.2f} KB/s"
        elif bytes_per_sec < 1024 * 1024 * 1024:
            return f"{bytes_per_sec / (1024 * 1024):.2f} MB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024 * 1024):.2f} GB/s"

    def _format_eta(self, seconds):
        """Convert seconds to human-readable ETA"""
        if seconds <= 0 or not self.active: # If not active, ETA is not meaningful
            return "N/A"
        seconds = int(seconds)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"
        elif minutes > 0:
            return f"{minutes:02d}m {seconds:02d}s"
        else:
            return f"{seconds:02d}s"

    def _download_chunk(self, url, start, end, path, chunk_index):
        """Chunk downloader with detailed logging and retry logic"""
        for attempt in range(3):
            try:
                while self.should_pause() and self.active:
                    self.total_paused_duration += (time.time() - self.pause_start_time) if self.pause_start_time else 0
                    self.pause_start_time = time.time() # Update pause start time
                    time.sleep(0.1)
                    if not self.active:  # Important: if the worker is stopped while paused, break
                        raise RuntimeError("Download stopped during pause.")
                
                # If unpaused, reset pause_start_time so it doesn't accumulate future non-paused time
                if not self.should_pause() and self.pause_start_time != 0.0:
                    self.total_paused_duration += (time.time() - self.pause_start_time)
                    self.pause_start_time = 0.0

                if not self.active:  # Check if stopped after pause loop
                    raise RuntimeError("Download stopped.")

                headers = HEADERS.copy()
                headers['Range'] = f'bytes={start}-{end}'
                
                response = requests.get(url, headers=headers, stream=True, timeout=15)
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                
                chunk_data = bytearray()
                for data in response.iter_content(1024 * 256):  # 256KB blocks
                    if not self.active: raise RuntimeError("Download stopped during chunk data reception.")
                    while self.should_pause():
                        self.total_paused_duration += (time.time() - self.pause_start_time) if self.pause_start_time else 0
                        self.pause_start_time = time.time() # Update pause start time
                        time.sleep(0.1)
                        if not self.active: raise RuntimeError("Download stopped during pause.")
                    
                    if not self.should_pause() and self.pause_start_time != 0.0:
                        self.total_paused_duration += (time.time() - self.pause_start_time)
                        self.pause_start_time = 0.0

                    chunk_data.extend(data)
                
                with open(path, 'r+b') as f:
                    f.seek(start)
                    f.write(chunk_data)
                
                return len(chunk_data) # Return bytes downloaded for this chunk
                
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    self.log_signal.emit(f"❌ Chunk {chunk_index+1} failed after 3 attempts due to network error: {str(e)}")
                    raise # Re-raise to be caught by as_completed
                
                self.log_signal.emit(
                    f"🔄 Retrying chunk {chunk_index+1} (attempt {attempt+1}/3) - Network error: {str(e)}"
                )
                time.sleep(1 + attempt) # Exponential backoff
            except Exception as e:
                if attempt == 2:
                    self.log_signal.emit(f"❌ Chunk {chunk_index+1} failed after 3 attempts: {str(e)}")
                    raise # Re-raise to be caught by as_completed
                
                self.log_signal.emit(
                    f"🔄 Retrying chunk {chunk_index+1} (attempt {attempt+1}/3) - Error: {str(e)}"
                )
                time.sleep(1 + attempt) # Exponential backoff
        return 0 # Should not reach here if exceptions are re-raised

    def _process_link(self, link):
        """Safe link processing (HTTP request, parsing, etc.)"""
        self.log_signal.emit(f"🔗 Fetching content for: {link[:60]}...")
        
        response = requests.get(link, headers=HEADERS, timeout=30)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Enhanced filename extraction
        file_name = self._extract_filename(soup, link)
        if not file_name:
            raise Exception("Could not determine a filename from the link/page.")

        # Enhanced download URL extraction
        download_url = self._extract_download_url(soup, link)
        if not download_url:
            raise Exception("No direct download URL found on the page.")
        
        return file_name, download_url

    def _single_thread_download(self, url, path, total_size):
        """Fallback single-thread download"""
        downloaded = 0
        
        try:
            with requests.get(url, stream=True, timeout=15) as response:
                response.raise_for_status()
                # If total_size was not determined by HEAD request, try to get it now
                if total_size == 0:
                    total_size = int(response.headers.get('content-length', 0))

                with open(path, 'wb') as f:
                    for data in response.iter_content(1024 * 1024):  # 1MB chunks
                        if not self.active:
                            self.log_signal.emit("Single-thread download cancelled.")
                            break

                        while self.should_pause():
                            self.total_paused_duration += (time.time() - self.pause_start_time) if self.pause_start_time else 0
                            self.pause_start_time = time.time()
                            time.sleep(0.1)
                            if not self.active: break # Check if stopped while paused

                        if not self.should_pause() and self.pause_start_time != 0.0:
                            self.total_paused_duration += (time.time() - self.pause_start_time)
                            self.pause_start_time = 0.0

                        f.write(data)
                        downloaded += len(data)
                        
                        self._update_speed_metrics(downloaded, total_size)
                
                if self.active: # Only update to 100% if not cancelled
                    self._update_speed_metrics(total_size, total_size)
        except requests.exceptions.RequestException as e:
            self.log_signal.emit(f"❌ Single-thread download failed: {str(e)}")
            raise # Re-raise to be caught by the main run loop
        except Exception as e:
            self.log_signal.emit(f"❌ Single-thread download failed: {str(e)}")
            raise # Re-raise

    def should_pause(self):
        with QMutexLocker(self._lock):
            return self._is_paused

    def _extract_filename(self, soup, fallback_url):
        """
        Extracts filename from various common locations (meta tags, og:title, title, URL).
        Cleans the filename for safe use on file systems.
        """
        # Try meta title/og:title
        for tag in soup.find_all('meta', attrs={'name': ['title', 'og:title']}):
            if tag.get('content'):
                title = tag['content']
                cleaned_title = re.sub(r'[\\/*?:"<>|]', "", title)
                if cleaned_title:
                    return cleaned_title.strip()
        
        # Try <title> tag
        if soup.title and soup.title.string:
            title = soup.title.string
            cleaned_title = re.sub(r'[\\/*?:"<>|]', "", title)
            if cleaned_title:
                return cleaned_title.strip()

        # Fallback to URL basename
        filename_from_url = os.path.basename(fallback_url).split("?")[0].split("#")[0]
        if filename_from_url:
            cleaned_filename = re.sub(r'[\\/*?:"<>|]', "", filename_from_url)
            if cleaned_filename:
                return cleaned_filename.strip()

        return "downloaded_file" # Default fallback if nothing suitable found

    def _extract_download_url(self, soup, original_link):
        """
        Robust URL extraction:
        1. Looks for window.open pattern (as in original)
        2. Looks for direct download links based on common attributes (e.g., download attribute, specific classes)
        3. Attempts to find the largest link from common file extensions.
        """
        # 1. Existing window.open logic (common on some redirect pages)
        for script in soup.find_all('script'):
            if 'function download' in script.text:
                match = re.search(r'window\.open\(["\'](https?://[^\s"\'\)]+)', script.text)
                if match:
                    return match.group(1)

        # 2. Look for direct download links in <a> tags
        potential_links = []
        # Look for links with 'download' attribute or specific keywords in href/text
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            link_text = a_tag.get_text(strip=True).lower()

            # Absolute URL check
            if not href.startswith(('http://', 'https://')):
                continue
            
            # Prioritize links with 'download' attribute
            if a_tag.has_attr('download') or 'download' in link_text or 'get file' in link_text:
                potential_links.append(href)
                
            # Look for common file extensions in the href
            if any(href.endswith(ext) for ext in ['.zip', '.rar', '.exe', '.iso', '.tar.gz', '.torrent', '.dmg', '.7z', '.gz']):
                potential_links.append(href)
        
        if potential_links:
            # Simple heuristic: pick the longest URL, often more specific
            return max(potential_links, key=len)

        # 3. Fallback: Check if the original link itself is a direct download or can be simplified
        if any(original_link.lower().endswith(ext) for ext in ['.zip', '.rar', '.exe', '.iso', '.tar.gz', '.torrent', '.dmg', '.7z', '.gz']):
            return original_link

        return None # No suitable download URL found


class AddLinksDialog(QtWidgets.QDialog):
    """
    Enhanced dialog for adding one or more download links manually with responsive design.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Links")
        self.setMinimumSize(500, 350)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        # Make dialog responsive
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        
        # Enhanced styling
        self.setStyleSheet("""
            QDialog {
                background-color: #23272E;
                color: #E0E0E0;
                border-radius: 15px;
            }
            QLabel {
                color: #40E0D0;
                font-weight: bold;
                font-size: 14px;
                margin-bottom: 10px;
            }
            QTextEdit {
                background-color: #1A1A1A;
                color: #E0E0E0;
                border: 2px solid #40E0D0;
                border-radius: 10px;
                padding: 15px;
                font-size: 12px;
                font-family: 'Consolas', 'Courier New', monospace;
                selection-background-color: #40E0D0;
                selection-color: #23272E;
            }
            QTextEdit:focus {
                border-color: #5DADE2;
                background-color: #1E1E1E;
            }
        """)

        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(25, 25, 25, 25)

        # Title with icon
        title_layout = QtWidgets.QHBoxLayout()
        if qta:
            icon_label = QtWidgets.QLabel()
            icon_pixmap = qta.icon('fa5s.plus-circle', color='#40E0D0').pixmap(32, 32)
            icon_label.setPixmap(icon_pixmap)
            title_layout.addWidget(icon_label)
        
        info_label = QtWidgets.QLabel("Add Download Links")
        info_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #40E0D0; margin-left: 10px;")
        title_layout.addWidget(info_label)
        title_layout.addStretch()
        layout.addLayout(title_layout)

        desc_label = QtWidgets.QLabel("Paste one or more download links below (one link per line):")
        desc_label.setStyleSheet("font-size: 12px; color: #C0C0C0; font-weight: normal;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        self.links_input = QtWidgets.QTextEdit()
        self.links_input.setPlaceholderText(
            "https://example.com/file1.zip\n"
            "https://example.com/file2.rar\n"
            "https://example.com/file3.iso\n\n"
            "• Supports HTTP/HTTPS links\n"
            "• Multiple files at once\n"
            "• Auto-validation"
        )
        layout.addWidget(self.links_input, 1)  # Allow expansion

        # Enhanced button layout
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        
        cancel_button = AnimatedButton("Cancel")
        cancel_button.setStyleSheet("""
            AnimatedButton {
                background-color: #6C7A89;
                border: 2px solid #5E6977;
                color: #E0E0E0;
                padding: 12px 25px;
                border-radius: 8px;
                font-weight: bold;
                font-size: 12px;
                min-width: 80px;
            }
            AnimatedButton:hover {
                background-color: #B0BEC5;
                color: #23272E;
            }
        """)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        add_button = AnimatedButton("Add Links")
        add_button.setStyleSheet("""
            AnimatedButton {
                background-color: #27AE60;
                border: 2px solid #1F8B4C;
                color: white;
                padding: 12px 25px;
                border-radius: 8px;
                font-weight: bold;
                font-size: 12px;
                min-width: 80px;
            }
            AnimatedButton:hover {
                background-color: #A5D6A7;
                color: #23272E;
            }
        """)
        if qta:
            add_button.setIcon(qta.icon('fa5s.plus', color='white'))
        add_button.clicked.connect(self.accept)
        button_layout.addWidget(add_button)
        
        layout.addLayout(button_layout)

    def get_links(self):
        """
        Returns a list of clean, non-empty links from the QTextEdit.
        """
        links_text = self.links_input.toPlainText()
        # Split by newline, strip whitespace from each line, and filter out empty lines
        links = [line.strip() for line in links_text.split('\n') if line.strip() and line.strip().startswith(('http://', 'https://'))]
        return links

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Adjust dialog size constraints based on screen size
        screen_size = QtWidgets.QApplication.primaryScreen().size()
        max_width = int(screen_size.width() * 0.7)
        max_height = int(screen_size.height() * 0.8)
        
        if self.width() > max_width:
            self.resize(max_width, self.height())
        if self.height() > max_height:
            self.resize(self.width(), max_height)


# class MainWindow(ResponsiveWidget):
#     """
#     Enhanced main application window for the downloader with responsive design.
#     """
#     THEMES = {
#         "🌙 Dark Blue": "dark_blue.xml",
#         "☀️ Light Blue": "light_blue.xml", 
#         "🟠 Dark Amber": "dark_amber.xml",
#         "🟡 Light Amber": "light_amber.xml",
#         "🟢 Dark Green": "dark_green.xml",
#         "🌿 Light Green": "light_green.xml",
#         "🟣 Dark Purple": "dark_purple.xml",
#         "🔮 Light Purple": "light_purple.xml",
#         "🔴 Dark Red": "dark_red.xml",
#         "🌹 Light Red": "light_red.xml",
#         "🟦 Dark Teal": "dark_teal.xml",
#         "💎 Light Teal": "light_teal.xml",
#         "🌊 Dark Cyan": "dark_cyan.xml",
#         "🧊 Light Cyan": "light_cyan.xml",
#         "⚫ Dark Grey": "dark_grey.xml",
#         "⚪ Light Grey": "light_grey.xml",
#     }

#     def __init__(self):
#         super().__init__()
#         self.setWindowTitle("🚀 Fuckingfast Downloader")
#         self.setMinimumSize(800, 500)  # Reduced minimum size for better mobile compatibility
        
#         # Responsive sizing based on screen
#         screen = QtWidgets.QApplication.primaryScreen().geometry()
#         default_width = min(1200, int(screen.width() * 0.8))
#         default_height = min(800, int(screen.height() * 0.8))
#         self.resize(default_width, default_height)
        
#         self.setStatusBar(QtWidgets.QStatusBar(self))
#         self.base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))

#         # Enhanced icon loading
#         try:
#             icon_path = os.path.join(self.base_path, "icons", "logo.ico")
#             if os.path.exists(icon_path):
#                 self.setWindowIcon(QtGui.QIcon(icon_path))
#             else:
#                 raise FileNotFoundError(f"Icon not found at {icon_path}")
#         except Exception as e:
#             if qta:
#                 self.setWindowIcon(qta.icon('fa5s.rocket', color='#40E0D0'))

#         # Enhanced font setup
#         font_family = "Segoe UI"
#         for preferred_font in ["Inter", "SF Pro Display", "Roboto", "Segoe UI"]:
#             if preferred_font in QFontDatabase().families():
#                 font_family = preferred_font
#                 break
        
#         default_font = QFont(font_family, 10)
#         default_font.setStyleHint(QFont.SansSerif)
#         QtWidgets.QApplication.setFont(default_font)

#         # Initialize UI components
#         self.init_ui_components()
        
#         # Load settings and apply theme
#         self.load_settings()
#         apply_stylesheet(self, theme=self.settings.get('theme', 'dark_blue.xml'))
#         self.apply_enhanced_styles()

#         # Setup main layout
#         self.setup_main_layout()
        
#         # Initialize worker and data
#         self.worker = None
#         self.download_queue = []
#         self.successful_downloads = []
#         self.failed_downloads = []
        
#         # Connect signals
#         self.connect_signals()
        
#         # Initial UI state
#         self.update_ui_for_idle()

class MainWindow(QtWidgets.QMainWindow):
    """
    Enhanced main application window for the downloader with responsive design.
    """

    THEMES = {
        "🌙 Dark Blue": "dark_blue.xml",
        "☀️ Light Blue": "light_blue.xml",
        "🟠 Dark Amber": "dark_amber.xml",
        "🟡 Light Amber": "light_amber.xml",
        "🟢 Dark Green": "dark_green.xml",
        "🌿 Light Green": "light_green.xml",
        "🟣 Dark Purple": "dark_purple.xml",
        "🔮 Light Purple": "light_purple.xml",
        "🔴 Dark Red": "dark_red.xml",
        "🌹 Light Red": "light_red.xml",
        "🟦 Dark Teal": "dark_teal.xml",
        "💎 Light Teal": "light_teal.xml",
        "🌊 Dark Cyan": "dark_cyan.xml",
        "🧊 Light Cyan": "light_cyan.xml",
        "⚫ Dark Grey": "dark_grey.xml",
        "⚪ Light Grey": "light_grey.xml",
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🚀 Fuckingfast Downloader")
        self.setMinimumSize(800, 500)

        # Responsive sizing
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            geometry = screen.geometry()
            default_width = min(1200, int(geometry.width() * 0.8))
            default_height = min(800, int(geometry.height() * 0.8))
        else:
            default_width, default_height = 1000, 600
        self.resize(default_width, default_height)

        # Status bar
        self.setStatusBar(QtWidgets.QStatusBar(self))

        # Base path
        self.base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))

        # Icon loading
        try:
            icon_path = os.path.join(self.base_path, "icons", "logo.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QtGui.QIcon(icon_path))
            else:
                raise FileNotFoundError
        except Exception:
            if qta:
                self.setWindowIcon(qta.icon('fa5s.rocket', color='#40E0D0'))

        # Font setup
        font_family = "Segoe UI"
        for preferred_font in ["Inter", "SF Pro Display", "Roboto", "Segoe UI"]:
            if preferred_font in QFontDatabase().families():
                font_family = preferred_font
                break

        default_font = QFont(font_family, 10)
        default_font.setStyleHint(QFont.SansSerif)
        default_font.setWeight(QFont.Normal)
        QtWidgets.QApplication.setFont(default_font)

        # Initialize components
        self.init_ui_components()

        # Load settings + theme
        self.settings = {}
        try:
            self.load_settings()
        except Exception as e:
            print("Settings load failed:", e)

        apply_stylesheet(self, theme=self.settings.get('theme', 'dark_blue.xml'))
        self.apply_enhanced_styles()

        # Setup layout
        self.setup_main_layout()

        # Worker & queues
        self.worker = None
        self.download_queue = []
        self.successful_downloads = []
        self.failed_downloads = []

        # Connect signals
        self.connect_signals()

        # Idle UI
        self.update_ui_for_idle()

    def init_ui_components(self):
        """Initialize all UI components with enhanced styling"""
        
        # Enhanced log area
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setAcceptRichText(True)
        self.log_text.setMinimumHeight(150)  # Reduced for mobile
        
        # Enhanced progress bar
        self.progress_bar = AnimatedProgressBar()
        
        # Enhanced status indicator
        self.status_indicator = StatusIndicator()
        
        # Enhanced buttons with icons
        self.load_btn = AnimatedButton("📂 Load Links")
        self.open_downloads_btn = AnimatedButton("📁 Downloads")
        self.download_btn = AnimatedButton("🚀 Download All")
        self.pause_btn = AnimatedButton("⏸️ Pause")
        self.resume_btn = AnimatedButton("▶️ Resume") 
        self.stop_btn = AnimatedButton("⏹️ Stop All")
        self.add_links_btn = AnimatedButton("➕ Add Links")
        self.clear_log_btn = AnimatedButton("🧹 Clear Log")
        
        # Enhanced controls
        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.setMinimumHeight(35)
        
        # Enhanced list widget
        self.list_widget = QListWidgetLinks()
        
        # Enhanced labels with better typography
        self.link_count_label = QtWidgets.QLabel("📊 Total Links: 0")
        self.file_label = QtWidgets.QLabel("📄 Current File: None")
        self.progress_detail_label = QtWidgets.QLabel("📈 Downloaded: 0.00 MB | Total: 0.00 MB")
        self.speed_label = QtWidgets.QLabel("⚡ Speed: 0.00 KB/s")
        self.eta_label = QtWidgets.QLabel("⏱️ ETA: N/A")
        
        # Social buttons
        self.github_button = AnimatedButton("🐙 GitHub Aryan")
        self.github_button = AnimatedButton("🐙 GitHub Yug")
        self.buymecoffee_button = AnimatedButton("☕ Coffee")
        
        # Info labels
        self.support_label = QtWidgets.QLabel("🎯 Check Out What I've Been Up To!")
        self.credits_label = QtWidgets.QLabel("")

        # Make labels word wrap for better responsive behavior
        for label in [self.file_label, self.progress_detail_label, self.speed_label, 
                     self.eta_label, self.support_label, self.credits_label]:
            label.setWordWrap(True)

    def setup_main_layout(self):
        """Setup the enhanced main layout with responsive behavior"""
        
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        
        # Use responsive splitter for main layout
        self.main_splitter = ResponsiveSplitter(Qt.Horizontal, central)
        
        # Left sidebar
        sidebar = self.create_sidebar()
        self.main_splitter.addWidget(sidebar)

        # Main content area
        content_area = self.create_content_area()
        self.main_splitter.addWidget(content_area)

        # Set initial splitter sizes (sidebar 25%, content 75%)
        self.main_splitter.setSizes([300, 900])
        self.main_splitter.setCollapsible(0, True)  # Allow sidebar collapse
        self.main_splitter.setCollapsible(1, False)  # Don't allow content collapse

        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.main_splitter)

    def update_layout(self):
        """Override responsive layout updates"""
        if hasattr(self, 'main_splitter'):
            if self.compact_mode:
                # In compact mode, prefer vertical layout or hide sidebar
                if self.current_width < 700:
                    self.main_splitter.setSizes([0, self.current_width])  # Hide sidebar
                else:
                    self.main_splitter.setSizes([250, self.current_width - 250])
            else:
                # Normal mode with balanced layout
                sidebar_width = max(280, int(self.current_width * 0.25))
                content_width = self.current_width - sidebar_width
                self.main_splitter.setSizes([sidebar_width, content_width])

    def create_sidebar(self):
        """Create enhanced sidebar with responsive behavior"""
        
        # Use scroll area for better mobile experience
        scroll_area = ResponsiveScrollArea()
        sidebar_widget = QtWidgets.QWidget()
        sidebar_widget.setMaximumWidth(350)
        sidebar_widget.setMinimumWidth(200)
        
        # Change sidebar background here
        sidebar_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #23272E, stop:1 #181C22);
                border-radius: 18px;
            }
        """)
        
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar_widget)
        sidebar_layout.setAlignment(Qt.AlignTop)
        sidebar_layout.setSpacing(15)  # Reduced spacing for mobile

        # Title with icon
        title_layout = QtWidgets.QHBoxLayout()
        if qta:
            icon_label = QtWidgets.QLabel()
            icon_pixmap = qta.icon('fa5s.plus-circle', color='#40E0D0').pixmap(32, 32)
            icon_label.setPixmap(icon_pixmap)
            title_layout.addWidget(icon_label)
        
        info_label = QtWidgets.QLabel("fuckingfast.co")
        info_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #40E0D0; margin-left: 10px;")
        title_layout.addWidget(info_label)
        title_layout.addStretch()
        sidebar_layout.addLayout(title_layout)

        desc_label = QtWidgets.QLabel("Downloader")
        desc_label.setStyleSheet("""
            QLabel {
                color: #B0BEC5;
                font-size: 14px;
                font-weight: 300;
                margin-bottom: 15px;
            }
        """)
        sidebar_layout.addWidget(desc_label)

        # Enhanced grouped controls with responsive button sizing
        file_group = self.create_control_group("📁 File Operations", [
            self.load_btn,
            self.open_downloads_btn
        ])
        sidebar_layout.addWidget(file_group)

        download_group = self.create_control_group("🎮 Download Controls", [
            self.download_btn,
            self.pause_btn,
            self.resume_btn,
            self.stop_btn
        ])
        sidebar_layout.addWidget(download_group)

        # Enhanced theme selector
        theme_group = self.create_theme_selector()
        sidebar_layout.addWidget(theme_group)

        sidebar_layout.addStretch()

        # Enhanced footer
        footer = self.create_sidebar_footer()
        sidebar_layout.addWidget(footer)

        scroll_area.setWidget(sidebar_widget)
        return scroll_area

    def create_control_group(self, title, buttons):
        """Create a styled group of control buttons with responsive sizing"""
        
        group = QtWidgets.QGroupBox(title)
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #353B48;
                border-radius: 15px;
                margin-top: 1ex;
                padding-top: 15px;
                background-color: rgba(38, 43, 51, 0.8);
                color: #40E0D0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 15px;
                background-color: #23272E;
                border-radius: 8px;
                color: #40E0D0;
                font-size: 12px;
                font-weight: bold;
            }
        """)
        
        layout = QtWidgets.QVBoxLayout(group)
        layout.setSpacing(8)  # Reduced spacing for mobile
        layout.setContentsMargins(12, 15, 12, 12)  # Adjusted margins
        
        for button in buttons:
            button.setMinimumHeight(38)  # Reduced height for mobile
            layout.addWidget(button)
            
        return group

    def create_theme_selector(self):
        """Create enhanced theme selection area with responsive design"""
        
        theme_group = QtWidgets.QGroupBox("🎨 Appearance")
        theme_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #353B48;
                border-radius: 15px;
                margin-top: 1ex;
                padding-top: 15px;
                background-color: rgba(38, 43, 51, 0.8);
                color: #40E0D0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 15px;
                background-color: #23272E;
                border-radius: 8px;
                color: #40E0D0;
                font-size: 12px;
                font-weight: bold;
            }
        """)
        
        layout = QtWidgets.QVBoxLayout(theme_group)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 15, 12, 12)
        
        self.theme_combo.addItems(sorted(self.THEMES.keys()))
        self.theme_combo.setStyleSheet("""
            QComboBox {
                border: 2px solid #40E0D0;
                border-radius: 8px;
                padding: 8px 12px;
                background-color: #1A1A1A;
                color: #E0E0E0;
                font-size: 12px;
                font-weight: 500;
                min-height: 22px;
            }
            QComboBox:hover {
                border-color: #5DADE2;
                background-color: #23272E;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QComboBox QAbstractItemView {
                border: 2px solid #40E0D0;
                background-color: #23272E;
                color: #E0E0E0;
                selection-background-color: #40E0D0;
                selection-color: #23272E;
                border-radius: 5px;
            }
        """)
        
        layout.addWidget(self.theme_combo)
        return theme_group

    def create_sidebar_footer(self):
        """Create enhanced sidebar footer with responsive behavior"""

        footer_widget = QtWidgets.QWidget()
        footer_layout = QtWidgets.QVBoxLayout(footer_widget)
        footer_layout.setSpacing(6)

        # Social buttons with enhanced styling
        social_layout = QtWidgets.QVBoxLayout()

        # --- Add two GitHub buttons ---
        self.github_button_aryan = AnimatedButton("🐙 GitHub Aryan")
        self.github_button_aryan.setMinimumHeight(35)
        self.github_button_aryan.setStyleSheet("""
            AnimatedButton {
                background-color: #333;
                border: 2px solid #555;
                color: white;
                text-align: left;
                padding: 6px 12px;
                border-radius: 8px;
                font-weight: 500;
                font-size: 11px;
            }
            AnimatedButton:hover {
                background-color: #555;
                border-color: #777;
                transform: translateY(-1px);
            }
        """)
        self.github_button_aryan.clicked.connect(
            lambda: webbrowser.open("https://github.com/devbyaryanvala")
        )
        social_layout.addWidget(self.github_button_aryan)

        self.github_button_yug = AnimatedButton("🐙 GitHub Yug")
        self.github_button_yug.setMinimumHeight(35)
        self.github_button_yug.setStyleSheet("""
            AnimatedButton {
                background-color: #333;
                border: 2px solid #555;
                color: white;
                text-align: left;
                padding: 6px 12px;
                border-radius: 8px;
                font-weight: 500;
                font-size: 11px;
            }
            AnimatedButton:hover {
                background-color: #555;
                border-color: #777;
                transform: translateY(-1px);
            }
        """)
        self.github_button_yug.clicked.connect(
            lambda: webbrowser.open("https://github.com/Yugpatel009")
        )
        social_layout.addWidget(self.github_button_yug)
        # --- End two GitHub buttons ---

        footer_layout.addLayout(social_layout)

        # Enhanced support label with responsive text
        self.support_label.setAlignment(Qt.AlignCenter)
        self.support_label.setWordWrap(True)
        self.support_label.setStyleSheet("""
            QLabel {
                font-size: 10px;
                font-weight: 500;
                color: #B0BEC5;
                margin: 8px 3px;
                line-height: 1.4;
            }
        """)
        footer_layout.addWidget(self.support_label)

        # Enhanced credits with responsive sizing
        self.credits_label.setText(
            "Made with <span style='color: #FF6B6B; font-size: 12px;'>♥</span> by "
            "<a style='color: #40E0D0; text-decoration: none; font-weight: bold;' href='https://aryanvala.site'>Aryan Vala</a>" "<br> and "
            "<a style='color: #40E0D0; text-decoration: none; font-weight: bold;' href='#'>Yug K.</a>"
        )
        self.credits_label.setOpenExternalLinks(True)
        self.credits_label.setAlignment(Qt.AlignCenter)
        self.credits_label.setStyleSheet("""
            QLabel {
                font-size: 9px;
                margin: 4px;
                padding: 6px;
                background-color: rgba(64, 224, 208, 0.1);
                border-radius: 6px;
                border: 1px solid rgba(64, 224, 208, 0.3);
            }
        """)
        footer_layout.addWidget(self.credits_label)

        return footer_widget
        """Create enhanced sidebar footer with responsive behavior"""
        
        footer_widget = QtWidgets.QWidget()
        footer_layout = QtWidgets.QVBoxLayout(footer_widget)
        footer_layout.setSpacing(6)
        
        # Social buttons with enhanced styling
        social_layout = QtWidgets.QVBoxLayout()
        
        self.github_button.setMinimumHeight(35)  # Reduced for mobile
        self.github_button.setStyleSheet("""
            AnimatedButton {
                background-color: #333;
                border: 2px solid #555;
                color: white;
                text-align: left;
                padding: 6px 12px;
                border-radius: 8px;
                font-weight: 500;
                font-size: 11px;
            }
            AnimatedButton:hover {
                background-color: #555;
                border-color: #777;
                transform: translateY(-1px);
            }
        """)
        
        social_layout.addWidget(self.github_button)
        footer_layout.addLayout(social_layout)
        
        # Enhanced support label with responsive text
        self.support_label.setAlignment(Qt.AlignCenter)
        self.support_label.setWordWrap(True)
        self.support_label.setStyleSheet("""
            QLabel {
                font-size: 10px;
                font-weight: 500;
                color: #B0BEC5;
                margin: 8px 3px;
                line-height: 1.4;
            }
        """)
        footer_layout.addWidget(self.support_label)

        # Enhanced credits with responsive sizing
        self.credits_label.setText(
            "Made with <span style='color: #FF6B6B; font-size: 12px;'>♥</span> by "
            "<a style='color: #40E0D0; text-decoration: none; font-weight: bold;' href='https://aryanvala.site'>Aryan Vala</a>" "<br> and "
            "<a style='color: #40E0D0; text-decoration: none; font-weight: bold;' href='#'>Yug K.</a>"
        )
        self.credits_label.setOpenExternalLinks(True)
        self.credits_label.setAlignment(Qt.AlignCenter)
        self.credits_label.setStyleSheet("""
            QLabel {
                font-size: 9px;
                margin: 4px;
                padding: 6px;
                background-color: rgba(64, 224, 208, 0.1);
                border-radius: 6px;
                border: 1px solid rgba(64, 224, 208, 0.3);
            }
        """)
        footer_layout.addWidget(self.credits_label)
        
        return footer_widget

    def create_content_area(self):
        """Create the main content area with responsive design"""
        
        content_widget = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_widget)
        content_layout.setSpacing(15)  # Reduced spacing

        # Create vertical splitter for responsive stacking
        self.content_splitter = ResponsiveSplitter(Qt.Vertical, content_widget)

        # Enhanced link list section
        link_section = self.create_link_section()
        self.content_splitter.addWidget(link_section)

        # Enhanced download info section
        download_section = self.create_download_section()
        self.content_splitter.addWidget(download_section)

        # Enhanced log section
        log_section = self.create_log_section()
        self.content_splitter.addWidget(log_section)

        # Set initial sizes (links 40%, download 35%, log 25%)
        self.content_splitter.setSizes([400, 350, 250])
        self.content_splitter.setCollapsible(0, False)  # Don't collapse links
        self.content_splitter.setCollapsible(1, False)  # Don't collapse download
        self.content_splitter.setCollapsible(2, True)   # Allow log collapse

        content_layout.addWidget(self.content_splitter)
        return content_widget

    def create_link_section(self):
        """Create enhanced link list section with responsive behavior"""
        
        link_group = QtWidgets.QGroupBox("Download Queue")
        link_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #353B48;
                border-radius: 15px;
                margin-top: 1ex;
                padding-top: 15px;
                background-color: rgba(38, 43, 51, 0.8);
                color: #40E0D0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 15px;
                background-color: #23272E;
                border-radius: 8px;
                color: #40E0D0;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        
        link_layout = QtWidgets.QVBoxLayout(link_group)
        link_layout.setSpacing(12)
        link_layout.setContentsMargins(15, 20, 15, 15)
        
        # Header with add button and count - responsive layout
        header_layout = QtWidgets.QHBoxLayout()
        
        self.add_links_btn.setMinimumHeight(35)  # Reduced for mobile
        self.add_links_btn.setStyleSheet("""
            AnimatedButton {
                background-color: #27AE60;
                border: 2px solid #1F8B4C;
                color: white;
                padding: 8px 16px;
                border-radius: 8px;
                font-weight: bold;
                font-size: 11px;
            }
            AnimatedButton:hover {
                background-color: #A5D6A7;
                color: #23272E;
            }
        """)
        header_layout.addWidget(self.add_links_btn)
        
        header_layout.addStretch()
        
        self.link_count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.link_count_label.setStyleSheet("""
            QLabel {
                font-weight: bold;
                color: #B0BEC5;
                font-size: 12px;
                padding: 6px 10px;
                background-color: rgba(64, 224, 208, 0.1);
                border-radius: 8px;
                border: 1px solid rgba(64, 224, 208, 0.3);
            }
        """)
        header_layout.addWidget(self.link_count_label)
        
        link_layout.addLayout(header_layout)
        
        # Enhanced list widget styling with responsive behavior
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: rgba(26, 26, 26, 0.9);
                color: #E0E0E0;
                border: 2px solid #40E0D0;
                border-radius: 12px;
                padding: 12px;
                font-size: 11px;
                font-family: 'Segoe UI', 'Inter', sans-serif;
                alternate-background-color: rgba(64, 224, 208, 0.05);
                selection-background-color: #40E0D0;
                selection-color: #23272E;
            }
            QListWidget::item {
                padding: 10px;
                margin: 2px 0;
                border-radius: 8px;
                border-left: 3px solid transparent;
            }
            QListWidget::item:hover {
                background-color: rgba(64, 224, 208, 0.15);
                border-left-color: #40E0D0;
            }
            QListWidget::item:selected {
                background-color: #40E0D0;
                color: #23272E;
                border-left-color: #23272E;
                font-weight: 500;
            }
            QScrollBar:vertical {
                border: none;
                background-color: rgba(64, 224, 208, 0.1);
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: #40E0D0;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #5DADE2;
            }
        """)
        link_layout.addWidget(self.list_widget)
        
        return link_group

    def create_download_section(self):
        """Create enhanced download progress section with responsive design"""
        
        download_group = QtWidgets.QGroupBox("Current Download")
        download_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #353B48;
                border-radius: 15px;
                margin-top: 1ex;
                padding-top: 15px;
                background-color: rgba(38, 43, 51, 0.8);
                color: #40E0D0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 15px;
                background-color: #23272E;
                border-radius: 8px;
                color: #40E0D0;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        
        download_layout = QtWidgets.QVBoxLayout(download_group)
        download_layout.setSpacing(12)
        download_layout.setContentsMargins(15, 20, 15, 15)

        # File info with responsive text
        self.file_label.setStyleSheet("""
            QLabel {
                font-weight: bold;
                font-size: 12px;
                color: #40E0D0;
                padding: 8px;
                background-color: rgba(64, 224, 208, 0.1);
                border-radius: 8px;
                border-left: 4px solid #40E0D0;
            }
        """)
        download_layout.addWidget(self.file_label)

        # Progress bar with responsive styling
        self.progress_bar.setStyleSheet("""
            AnimatedProgressBar {
                border: 2px solid #353B48;
                border-radius: 10px;
                text-align: center;
                color: #FFFFFF;
                background-color: rgba(26, 26, 26, 0.9);
                font-weight: bold;
                font-size: 12px;
                min-height: 28px;
            }
            AnimatedProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #40E0D0, stop:0.5 #5DADE2, stop:1 #40E0D0);
                border-radius: 8px;
            }
        """)
        download_layout.addWidget(self.progress_bar)

        # Status indicator
        download_layout.addWidget(self.status_indicator)

        # Info grid with responsive layout
        info_widget = QtWidgets.QWidget()
        info_layout = QtWidgets.QGridLayout(info_widget)
        info_layout.setSpacing(8)

        info_labels = [
            (self.progress_detail_label, 0, 0, 1, 2),
            (self.speed_label, 1, 0),
            (self.eta_label, 1, 1)
        ]

        for label, *pos in info_labels:
            label.setStyleSheet("""
                QLabel {
                    font-weight: 500;
                    color: #C0C0C0;
                    font-size: 11px;
                    padding: 6px 10px;
                    background-color: rgba(53, 59, 72, 0.5);
                    border-radius: 6px;
                    border: 1px solid rgba(64,  224, 208, 0.2);
                }
            """)
            label.setAlignment(Qt.AlignCenter)
            info_layout.addWidget(label, *pos)

        download_layout.addWidget(info_widget)
        return download_group

    def create_log_section(self):
        """Create enhanced log section with responsive design"""
        
        log_group = QtWidgets.QGroupBox("Activity Log")
        log_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #353B48;
                border-radius: 15px;
                margin-top: 1ex;
                padding-top: 15px;
                background-color: rgba(38, 43, 51, 0.8);
                color: #40E0D0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 15px;
                background-color: #23272E;
                border-radius: 8px;
                               color: #40E0D0;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        
        log_layout = QtWidgets.QVBoxLayout(log_group)
        log_layout.setSpacing(8)
        log_layout.setContentsMargins(15, 20, 15, 15)

        # Log text area with enhanced responsive styling
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: rgba(24, 28, 34, 0.95);
                color: #C0C0C0;
                border: 2px solid #40E0D0;
                border-radius: 10px;
                padding: 12px;
                font-size: 10px;
                font-family: 'Consolas', 'SF Mono', 'Monaco', 'Menlo', monospace;
                line-height: 1.4;
                selection-background-color: #40E0D0;
                selection-color: #23272E;
            }
            QScrollBar:vertical {
                border: none;
                background-color: rgba(64, 224, 208, 0.1);
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: #40E0D0;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #5DADE2;
            }
        """)
        log_layout.addWidget(self.log_text)

        # Clear button with responsive sizing
        self.clear_log_btn.setMinimumHeight(30)
        self.clear_log_btn.setStyleSheet("""
            AnimatedButton {
                background-color: #6C7A89;
                border: 2px solid #5E6977;
                color: #E0E0E0;
                padding: 6px 14px;
                border-radius: 8px;
                font-weight: 500;
                font-size: 10px;
            }
            AnimatedButton:hover {
                background-color: #B0BEC5;
                color: #23272E;
            }
        """)
        log_layout.addWidget(self.clear_log_btn)
        
        return log_group

    def apply_enhanced_styles(self):
        """Apply comprehensive enhanced styling with responsive considerations"""
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #181C22, stop:0.5 #23272E, stop:1 #181C22);
            }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #353B48;
                border-radius: 15px;
                margin-top: 1ex;
                padding-top: 15px;
                background-color: rgba(38, 43, 51, 0.92);
                color: #40E0D0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 15px;
                background-color: #23272E;
                border-radius: 8px;
                color: #40E0D0;
                font-size: 13px;
                font-weight: bold;
            }
            AnimatedButton, QPushButton {
                background-color: #353B48;
                border: 2px solid #40E0D0;
                color: #E0E0E0;
                padding: 10px 15px;
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
                outline: none;
                qproperty-iconSize: 16px;
                min-height: 36px;
            }
            AnimatedButton:hover, QPushButton:hover {
                background-color: #40E0D0;
                color: #23272E;
                border-color: #5DADE2;
            }
            AnimatedButton:pressed, QPushButton:pressed {
                background-color: #2C3E50;
                border-color: #40E0D0;
            }
            AnimatedButton:disabled, QPushButton:disabled {
                background-color: #2C3E50;
                border-color: #353B48;
                color: #777777;
            }
            QComboBox {
                border: 2px solid #40E0D0;
                border-radius: 8px;
                padding: 8px 12px;
                background-color: #1A1A1A;
                color: #E0E0E0;
                font-size: 12px;
                font-weight: 500;
                min-height: 22px;
            }
            QComboBox:hover {
                border-color: #5DADE2;
                background-color: #23272E;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QComboBox QAbstractItemView {
                border: 2px solid #40E0D0;
                background-color: #23272E;
                color: #E0E0E0;
                selection-background-color: #40E0D0;
                selection-color: #23272E;
                border-radius: 5px;
            }
            QLabel {
                color: #E0E0E0;
                font-size: 12px;
            }
            QTextEdit {
                background-color: #181C22;
                color: #C0C0C0;
                border: 2px solid #40E0D0;
                border-radius: 10px;
                padding: 12px;
                font-size: 11px;
                font-family: 'Consolas', 'SF Mono', 'Monaco', 'Menlo', monospace;
            }
            QScrollBar:vertical {
                border: none;
                background-color: rgba(64, 224, 208, 0.1);
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: #40E0D0;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #5DADE2;
            }
            QStatusBar {
                background-color: rgba(35, 39, 46, 0.9);
                color: #B0BEC5;
                border-top: 1px solid #353B48;
                font-size: 11px;
                padding: 3px;
            }
        """)

    def resizeEvent(self, event: QtGui.QResizeEvent):
        """Handle window resize with responsive layout updates"""
        super().resizeEvent(event)

        # Always update current dimensions
        self.current_width = self.width()
        self.current_height = self.height()

        # Update button text based on window width for ultra-compact mode
        if hasattr(self, 'download_btn'):
            if self.current_width < 600:
                self.download_btn.setText("🚀 Download")
                self.add_links_btn.setText("➕ Add")
                self.open_downloads_btn.setText("📁 Files")
            else:
                self.download_btn.setText("🚀 Download All")
                self.add_links_btn.setText("➕ Add Links")
                self.open_downloads_btn.setText("📁 Downloads")

    def connect_signals(self):
        """Connect all UI signals"""
        try:
            if hasattr(self, "load_btn"):
                self.load_btn.clicked.connect(self.load_links)
            if hasattr(self, "download_btn"):
                self.download_btn.clicked.connect(self.download_all)
            if hasattr(self, "pause_btn"):
                self.pause_btn.clicked.connect(self.pause_download)
            if hasattr(self, "resume_btn"):
                self.resume_btn.clicked.connect(self.resume_download)
            if hasattr(self, "stop_btn"):
                self.stop_btn.clicked.connect(self.stop_download)
            if hasattr(self, "open_downloads_btn"):
                self.open_downloads_btn.clicked.connect(self.open_downloads_folder)
            if hasattr(self, "add_links_btn"):
                self.add_links_btn.clicked.connect(self.add_links_manually)
            if hasattr(self, "clear_log_btn") and hasattr(self, "log_text"):
                self.clear_log_btn.clicked.connect(self.log_text.clear)
            if hasattr(self, "theme_combo"):
                self.theme_combo.currentIndexChanged.connect(self.change_theme)
            if hasattr(self, "github_button"):
                self.github_button.clicked.connect(
                    lambda: webbrowser.open("https://github.com/devbyaryanvala")
                )
            if hasattr(self, "github_button"):
                self.github_button.clicked.connect(
                    lambda: webbrowser.open("https://github.com/Yugpatel009")
                )
            if hasattr(self, "list_widget"):
                self.list_widget.itemDoubleClicked.connect(self.copy_link_to_clipboard)
                self.list_widget.model().rowsMoved.connect(self.update_link_numbers)

        except Exception as e:
            print("⚠️ Signal connection error:", e)

    def load_settings(self):
        """Loads application settings from a JSON file."""
        self.settings = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    self.settings = json.load(f)
            except json.JSONDecodeError as e:
                print(f"Error reading config file: {e}. Using default settings.")
        
    def save_settings(self):
        """Saves current application settings to a JSON file."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            self.log(f"Error saving config file: {e}")

    def change_theme(self, index):
        theme_name = self.theme_combo.currentText()
        theme_file = self.THEMES.get(theme_name, "dark_blue.xml")
        apply_stylesheet(self, theme=theme_file)
        self.settings['theme'] = theme_file
        self.save_settings()
        self.log(f"Theme changed to '{theme_name}'.")
        self.apply_enhanced_styles()

    def update_ui_for_idle(self):
        self.download_btn.setEnabled(True)
        self.load_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.add_links_btn.setEnabled(True)
        
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")
        self.file_label.setText("Current File: None")
        self.progress_detail_label.setText("Downloaded: 0.00 MB | Total: 0.00 MB")
        self.speed_label.setText("Speed: 0.00 KB/s")
        self.eta_label.setText("ETA: N/A")
        self.status_indicator.set_status("Ready", "green")
        self.update_link_numbers()

    def update_ui_for_downloading(self):
        self.download_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)  
        self.stop_btn.setEnabled(True)
        self.add_links_btn.setEnabled(False)

    def update_ui_for_paused(self):
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)

    def update_ui_for_resumed(self):
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    # Rest of the methods remain the same as in the original code...
    # (load_links, add_links_manually, copy_link_to_clipboard, log, download_all, 
    #  pause_download, resume_download, stop_download, open_downloads_folder,
    #  update_progress, update_speed, update_file, update_status, mark_link_processing,
    #  update_link_numbers, handle_link_completed, handle_link_failed, 
    #  handle_session_finished, remove_selected_links, clear_all_links,
    #  _update_input_file, show_notification, closeEvent)

    def load_links(self):
        if not os.path.exists(INPUT_FILE):
            with open(INPUT_FILE, 'w') as f:
                f.write("# Add download links here (lines starting with # are comments)\n")
            QtWidgets.QMessageBox.information(self, "Info", f"Input file '{INPUT_FILE}' not found. It has been created. Please add links and reload.")
            return

        self.list_widget.clear()
        self.download_queue.clear()
        with open(INPUT_FILE, 'r') as f:
            for line in f:
                stripped_line = line.strip()
                if stripped_line and not stripped_line.startswith("#"):
                    self.list_widget.addItem(stripped_line)
                    self.download_queue.append(stripped_line)
        self.log(f"Loaded {len(self.download_queue)} link(s) from {INPUT_FILE}")
        self.update_link_numbers()
        self.update_ui_for_idle()

    def add_links_manually(self):
        """Opens a dialog to add one or more links manually."""
        dialog = AddLinksDialog(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            links = dialog.get_links()
            if not links:
                self.statusBar().showMessage("No valid links entered.", 2000)
                return

            added_count = 0
            for link in links:
                if link not in self.download_queue:
                    self.list_widget.addItem(link)
                    self.download_queue.append(link)
                    added_count += 1
                else:
                    self.log(f"Link already in queue (skipped): {link[:60]}...")

            if added_count > 0:
                self.update_link_numbers()
                self._update_input_file()
                self.log(f"Added {added_count} new link(s).")
            else:
                self.statusBar().showMessage("No new links were added (all were duplicates).", 3000)

    def copy_link_to_clipboard(self, item):
        link = item.text().split(". ", 1)[-1] if ". " in item.text() else item.text()
        QtWidgets.QApplication.clipboard().setText(link)
        self.statusBar().showMessage("Link copied to clipboard", 2000)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        colored_message = colorize_log_message(message)
        if hasattr(self, 'log_text'):
            self.log_text.append(f"<p style='font-weight:500; font-family: \"Consolas\", monospace; font-size:10pt; margin:2px 0; padding:2px;'><span style='color:#666; font-size:9pt;'>[{timestamp}]</span> {colored_message}</p>")
            self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
        else:
            print(f"[{timestamp}] {message}")

    def download_all(self):
        if self.worker and self.worker.isRunning():
            self.log("Stopping current download session before starting new one...")
            self.worker.stop()
            self.worker.wait(3000)

        if not self.download_queue:
            QtWidgets.QMessageBox.information(self, "Info", "No links to download. Please load links first.")
            return
        
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.file_label.setText("Current File: None")
        self.progress_detail_label.setText("Downloaded: 0.00 MB | Total: 0.00 MB")
        self.speed_label.setText("Speed: 0.00 KB/s")
        self.eta_label.setText("ETA: N/A")
        self.successful_downloads = []
        self.failed_downloads = []

        self.worker = DownloaderWorker(self.download_queue[:])
        self.worker.log_signal.connect(self.log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.file_signal.connect(self.update_file)
        self.worker.status_signal.connect(self.update_status)
        self.worker.speed_signal.connect(self.update_speed)
        self.worker.link_completed_signal.connect(self.handle_link_completed)
        self.worker.link_failed_signal.connect(self.handle_link_failed)
        self.worker.session_finished_signal.connect(self.handle_session_finished)
        self.worker.link_processing_signal.connect(self.mark_link_processing)
        
        self.worker.start()
        self.update_ui_for_downloading()
        self.log("Download session initiated.")

    def pause_download(self):
        if self.worker and self.worker.isRunning():
            self.worker.pause()
            self.update_ui_for_paused()
            self.status_indicator.set_status("Paused", "gold")

    def resume_download(self):
        if self.worker and self.worker.isRunning():
            self.worker.resume_download()
            self.update_ui_for_resumed()
            self.status_indicator.set_status("Downloading...", "blue")

    def stop_download(self):
        if self.worker and self.worker.isRunning():
            self.log("Requesting worker to stop...")
            self.worker.stop()
            self.status_indicator.set_status("Stopping...", "red")
            self.statusBar().showMessage("Download stopping...", 3000)
        else:
            self.log("No active download to stop.")
            self.update_ui_for_idle()

    def open_downloads_folder(self):
        """Opens the downloads folder using the OS default file manager."""
        if not os.path.exists(DOWNLOADS_FOLDER):
            os.makedirs(DOWNLOADS_FOLDER)
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(DOWNLOADS_FOLDER)))
        self.log(f"Opened downloads folder: {os.path.abspath(DOWNLOADS_FOLDER)}")

    def update_progress(self, downloaded, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(downloaded)
        
        if total > 0:
            percent = (downloaded * 100 / total)
            self.progress_bar.setFormat(f"{percent:.1f}% - {self.worker._format_bytes(downloaded)} / {self.worker._format_bytes(total)}")
        else:
            self.progress_bar.setFormat("0%")

        self.progress_detail_label.setText(
            f"Downloaded: {self.worker._format_bytes(downloaded)} | "
            f"Total: {self.worker._format_bytes(total)}"
        )

    def update_speed(self, current_speed_bps, overall_speed_bps, eta_seconds):
        """Handle speed updates with proper unit formatting"""
        self.speed_label.setText(f"Speed: {self.worker._format_speed(overall_speed_bps)}")
        self.eta_label.setText(f"ETA: {self.worker._format_eta(eta_seconds)}")

    def update_file(self, filename):
        self.file_label.setText(f"Current File: {filename}")

    def update_status(self, status):
        if "Paused" in status:
            self.status_indicator.set_status(f"⏸ {status}", "gold")
        elif "Downloading" in status or "Resuming" in status or "Fetching" in status:
            self.status_indicator.set_status(f"⬇ {status}", "blue")
        elif "Idle" in status or "Finished" in status or "Completed" in status:
            self.status_indicator.set_status(f"✓ {status}", "green")
        elif "Error" in status or "Failed" in status or "Stopping" in status:
            self.status_indicator.set_status(f"✗ {status}", "red")
        else:
            self.status_indicator.set_status(status, "green")

    def mark_link_processing(self, processing_link):
        """Marks the currently processing link in the QListWidget with a distinctive color."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            current_link_in_list = item.text().split(". ", 1)[-1]
            if current_link_in_list == processing_link:
                item.setForeground(QColor("#40E0D0"))
                item.setToolTip("Currently downloading...")
            else:
                if item.foreground().color() != QColor("red"):
                    item.setForeground(QColor(Qt.white))
                    item.setToolTip("")

    def update_link_numbers(self):
        """Re-numbers the items in the QListWidget based on their current index and updates total count."""
        self.list_widget.blockSignals(True)  
        try:
            current_links_in_widget = []
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                original_link = item.text().split(". ", 1)[-1] if ". " in item.text() else item.text()
                
                current_color = item.foreground().color() if item.foreground() else QtGui.QColor(Qt.white)
                current_font = item.font() if item.font() else self.list_widget.font()
                
                item.setText(f"{i + 1}. {original_link}")
                item.setForeground(current_color)
                item.setFont(current_font)
                current_links_in_widget.append(original_link)
            
            self.download_queue = current_links_in_widget

        finally:
            self.list_widget.blockSignals(False)
            self.link_count_label.setText(f"Total Links: {self.list_widget.count()}")

    def handle_link_completed(self, link_completed):
        """Handles a link that has successfully completed download."""
        removed_from_list = False
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            current_link_in_list = item.text().split(". ", 1)[-1]  
            if current_link_in_list == link_completed:
                self.list_widget.takeItem(i)
                self.log(f"Removed completed link '{link_completed[:50]}...' from list.")
                removed_from_list = True
                break
        
        if removed_from_list:
            self.download_queue = [self.list_widget.item(i).text().split(". ", 1)[-1] for i in range(self.list_widget.count())]
            self._update_input_file()
            self.update_link_numbers()

        self.show_notification("Download Completed!", f"Successfully downloaded: {link_completed.split('/')[-1]}")

    def handle_link_failed(self, failed_link, error_message):
        """Marks a link in the list widget as failed (red color) and logs the error."""
        found_in_list = False
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            current_link_in_list = item.text().split(". ", 1)[-1]
            if current_link_in_list == failed_link:
                item.setForeground(QColor("red"))
                item.setToolTip(f"Failed: {error_message}")
                self.log(f"Link '{failed_link[:50]}...' failed: {error_message}")
                found_in_list = True
                break
        if not found_in_list:
            self.log(f"Failed link '{failed_link[:50]}...' not found in list (might have been removed). Error: {error_message}")

        self.status_indicator.set_status("Error: Check Log", "red")

    def handle_session_finished(self, completed_links, failed_links):
        """Called when the DownloaderWorker finishes its entire session."""
        self.log(f"Download session finished. {len(completed_links)} completed, {len(failed_links)} failed.")
        self.update_ui_for_idle()

        summary_msg = (
            f"Download Session Completed!\n\n"
            f"Successfully Downloaded: {len(completed_links)} file(s)\n"
            f"Failed Downloads: {len(failed_links)} file(s)"
        )
        if failed_links:
            summary_msg += "\n\nFailed links (check log for details):\n"
            for link in failed_links[:5]:
                summary_msg += f"- {link[:70]}...\n"
            if len(failed_links) > 5:
                summary_msg += f"... and {len(failed_links) - 5} more."
        
        QtWidgets.QMessageBox.information(self, "Download Summary", summary_msg)

    def remove_selected_links(self):
        """Removes selected links from the list widget and input.txt."""
        selected_items = self.list_widget.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.information(self, "Info", "No links selected to remove.")
            return

        reply = QtWidgets.QMessageBox.question(self, 'Remove Links', 
                                            f"Are you sure you want to remove {len(selected_items)} selected link(s)?", 
                                            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.Yes:
            for item in reversed(selected_items):
                row = self.list_widget.row(item)
                link_to_remove = item.text().split(". ", 1)[-1]
                self.list_widget.takeItem(row)
                self.log(f"Removed '{link_to_remove[:50]}...' from list.")

            self.download_queue = [self.list_widget.item(i).text().split(". ", 1)[-1] for i in range(self.list_widget.count())]
            self._update_input_file()
            self.update_link_numbers()

    def clear_all_links(self):
        """Clears all links from the list widget and input.txt."""
        if not self.download_queue:
            QtWidgets.QMessageBox.information(self, "Info", "The list is already empty.")
            return

        reply = QtWidgets.QMessageBox.question(self, 'Clear All Links', 
                                            "Are you sure you want to clear ALL links from the list and input.txt?", 
                                            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.Yes:
            self.list_widget.clear()
            self.download_queue.clear()
            self._update_input_file()
            self.log("All links cleared from list and input.txt.")
            self.update_ui_for_idle()

    def _update_input_file(self):
        """Rewrites the input.txt file with the current links in the download queue."""
        try:
            with open(INPUT_FILE, 'w') as f:
                f.write("# Add download links here (lines starting with # are comments)\n")
                for link in self.download_queue:
                    f.write(link + "\n")
            self.log(f"{INPUT_FILE} updated successfully.")
        except Exception as e:
            self.log(f"Error writing to {INPUT_FILE}: {e}")

    def show_notification(self, title, message):
        """Displays a desktop notification with enhanced styling."""
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            try:
                icon = self.windowIcon()
                if icon.isNull() and qta:
                    icon = qta.icon('fa5s.download', color='#40E0D0')
                elif icon.isNull():
                    icon = QtGui.QIcon(":/qt-project.org/qmessagebox/images/information.png")

                tray_icon = QtWidgets.QSystemTrayIcon(icon, self)
                tray_icon.show()
                tray_icon.showMessage(title, message, QtWidgets.QSystemTrayIcon.Information, 5000)
                QtCore.QTimer.singleShot(6000, tray_icon.deleteLater)
            except Exception as e:
                self.log(f"Error showing tray notification: {e}. Falling back to QMessageBox.")
                QtWidgets.QMessageBox.information(self, title, message)
        else:
            # Enhanced message box styling
            msg_box = QtWidgets.QMessageBox(self)
            msg_box.setWindowTitle(title)
            msg_box.setText(message)
            msg_box.setStyleSheet("""
                QMessageBox {
                    background-color: #23272E;
                    color: #E0E0E0;
                }
                QMessageBox QPushButton {
                    background-color: #40E0D0;
                    color: #23272E;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                    min-width: 70px;
                }
                QMessageBox QPushButton:hover {
                    background-color: #5DADE2;
                }
            """)
            msg_box.exec_()

    def closeEvent(self, event):
        """Enhanced cleanup on window close with fade animation"""
        if self.worker and self.worker.isRunning():
            # Show confirmation dialog with custom styling
            msg_box = QtWidgets.QMessageBox(self)
            msg_box.setWindowTitle("Confirm Exit")
            msg_box.setText("Downloads are still in progress. Are you sure you want to exit?")
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            msg_box.setDefaultButton(QtWidgets.QMessageBox.No)
            msg_box.setStyleSheet("""
                QMessageBox {
                    background-color: #23272E;
                    color: #E0E0E0;
                }
                QMessageBox QPushButton {
                    background-color: #40E0D0;
                    color: #23272E;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                    min-width: 70px;
                }
                QMessageBox QPushButton:hover {
                    background-color: #5DADE2;
                }
            """)
            
            if msg_box.exec_() == QtWidgets.QMessageBox.No:
                event.ignore()
                return
                
            self.log("Stopping download worker on application exit...")
            self.worker.stop()
            self.worker.wait(5000)
            
        self.save_settings()
        event.accept()


def main():
    """Enhanced application entry point with better error handling and responsive design"""
    
    # Enable high DPI scaling
    QtWidgets.QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Fuckingfast Downloader")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("Aryan Vala")
    
    # Set application style
    app.setStyle('Fusion')
    
    # Enhanced font setup with fallbacks
    font_family = "Segoe UI"
    available_fonts = QFontDatabase().families()
    
    # Preferred fonts in order of preference
    preferred_fonts = [
        "Inter", "SF Pro Display", "SF Pro Text", 
        "Roboto", "Ubuntu", "Segoe UI", "Helvetica Neue"
    ]
    
    for font in preferred_fonts:
        if font in available_fonts:
            font_family = font
            break
    
    # Set up application font with better metrics for different screen sizes
    screen = app.primaryScreen()
    screen_dpi = screen.logicalDotsPerInch()
    base_font_size = 10
    
    # Adjust font size based on DPI for better readability
    if screen_dpi > 120:  # High DPI display
        base_font_size = 9
    elif screen_dpi < 96:  # Low DPI display
        base_font_size = 11
    
    default_font = QFont(font_family, base_font_size)
    default_font.setStyleHint(QFont.SansSerif)
    default_font.setWeight(QFont.Normal)
    app.setFont(default_font)
    
    # Create and show main window
    try:
        window = MainWindow()
        
        # Set initial theme from saved settings
        current_theme_file = window.settings.get('theme', 'dark_blue.xml')
        for name, file in window.THEMES.items():
            if file == current_theme_file:
                window.theme_combo.setCurrentText(name)
                break
        
        # Center window on screen with responsive positioning
        screen_geometry = app.primaryScreen().geometry()
        window_geometry = window.geometry()
        
        # Calculate center position
        x = (screen_geometry.width() - window_geometry.width()) // 2
        y = (screen_geometry.height() - window_geometry.height()) // 2
        
        # Ensure window is not positioned off-screen
        x = max(0, min(x, screen_geometry.width() - window_geometry.width()))
        y = max(0, min(y, screen_geometry.height() - window_geometry.height()))
        
        window.move(x, y)
        window.show()
        
        # Show welcome message
        window.log("Welcome to Fuckingfast Downloader v2.0!")
        window.log("Enhanced responsive UI with improved mobile compatibility.")
        
        # Handle system scaling
        if screen.devicePixelRatio() > 1.0:
            window.log(f"High DPI display detected (ratio: {screen.devicePixelRatio():.1f})")
        
        sys.exit(app.exec_())
        
    except Exception as e:
        print(f"Error starting application: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
