#!/usr/bin/env python3
"""
Fucking Fast Downloader
A PyQt5 application to download files from provided links.

Usage:
  - Click "Load Links" to import download links from input.txt.
  - Double-click any link in the list to copy it to clipboard.
  - Click "Download All" to start downloading.
  - Use the Pause/Resume buttons to control downloads.
"""

import os
import re
import sys
import time
import webbrowser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QFont, QFontDatabase, QDesktopServices
from qt_material import apply_stylesheet

# Global configuration
INPUT_FILE = "input.txt"
DOWNLOADS_FOLDER = "downloads"
MAX_WORKERS = 6 # Maximum concurrent download chunks

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
    elif "paused" in msg_lower:
        color = "#FFD700"  # Gold
        if "⏸️" not in message:
            emoji = "⏸️ "
    elif "resumed" in msg_lower:
        color = "#00BFFF"  # DeepSkyBlue
        if "▶️" not in message:
            emoji = "▶️ "
    elif "downloading" in msg_lower or "⬇️" in message:
        color = "#1E90FF"  # DodgerBlue
        if "⬇️" not in message:
            emoji = "⬇️ "
    elif "processing link" in msg_lower or "fetching" in msg_lower:
        color = "#40E0D0"  # Turquoise
        if "🔗" not in message:
            emoji = "🔗 "
    elif "loaded" in msg_lower or "imported" in msg_lower:
        color = "#DA70D6"  # Orchid
        if "📥" not in message:
            emoji = "📥 "
    elif "removed" in msg_lower or "deleted" in msg_lower:
        color = "#A9A9A9" # DarkGray
        if "🗑️" not in message:
            emoji = "🗑️ "
    else:
        color = "#FFFFFF"  # Default to white if no keywords match

    return f"<span style='color:{color};'>{emoji}{message}</span>"

# ----------------------- GUI Code -----------------------
class DownloaderWorker(QtCore.QThread):
    """
    Thread-safe worker with proper signal handling
    """
    log_signal = QtCore.pyqtSignal(str)
    progress_signal = QtCore.pyqtSignal(int, int) # downloaded_bytes, total_bytes
    file_signal = QtCore.pyqtSignal(str) # current filename
    status_signal = QtCore.pyqtSignal(str) # overall status text
    speed_signal = QtCore.pyqtSignal(float) # speed in MB/s
    link_completed_signal = QtCore.pyqtSignal(str) # link that finished successfully
    link_failed_signal = QtCore.pyqtSignal(str, str) # link, error_message
    session_finished_signal = QtCore.pyqtSignal()

    def __init__(self, links, parent=None):
        super().__init__(parent)
        self.links = links
        self._is_paused = False
        self._lock = QtCore.QMutex()
        self.active = True # Controls the main loop and threads
        self.current_download_url = None # To potentially allow stopping current download
        self.session_links = links # Keep a copy of original links for session summary

    def pause(self):
        with QtCore.QMutexLocker(self._lock):
            self._is_paused = True
        self.status_signal.emit("Paused")
        self.log_signal.emit("⏸ Download paused.")

    def resume_download(self):
        with QtCore.QMutexLocker(self._lock):
            self._is_paused = False
        self.status_signal.emit("Resuming...")
        self.log_signal.emit("▶ Download resumed.")

    def stop(self):
        self.active = False
        # Wake up any sleeping threads if paused
        self.resume_download() 
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
                f"   URL: {link[:70]}{'...' if len(link) > 70 else ''}"
            )
            self.status_signal.emit(f"Fetching: {link[:30]}...")

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
                    f"   Name: {file_name}\n"
                    f"   Size: {remote_size_mb:.2f} MB"
                )
                self.file_signal.emit(file_name) # Update UI with current file
                
                dl_start = time.time()
                self._download_file(download_url, output_path)
                
                self.log_signal.emit(
                    f"✅ Download completed\n"
                    f"   Time: {time.time() - dl_start:.1f}s\n"
                    f"   Path: {output_path}"
                )
                self.link_completed_signal.emit(link) # Signal successful completion
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Network error: {e}"
                self.log_signal.emit(f"❌ Error for {link[:50]}...: {error_msg}")
                self.link_failed_signal.emit(link, error_msg)
            except Exception as e:
                error_msg = f"General error: {e}"
                self.log_signal.emit(f"❌ Error for {link[:50]}...: {error_msg}")
                self.link_failed_signal.emit(link, error_msg)
            finally:
                self.current_download_url = None # Clear current download reference

        total_time = time.time() - start_session
        self.log_signal.emit(
            f"🏁 Session finished\n"
            f"   Duration: {total_time:.1f}s\n"
            f"   Processed: {len(current_links_to_process)} files"
        )
        self.status_signal.emit("Idle")
        self.session_finished_signal.emit() # Signal that the entire session is done


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

        self.dl_start_time = time.time()
        self.last_update = self.dl_start_time
        self.total_paused_time = 0.0
        self.last_bytes = 0
        
        self.status_signal.emit(f"Downloading: {os.path.basename(path)}")

        if total_size > 0 and total_size > 1024 * 1024 and accept_ranges:  # Multi-thread for >1MB
            self._chunked_download(url, path, total_size)
        else:
            self._single_thread_download(url, path)

    def _chunked_download(self, url, path, total_size):
        """Threaded download with accurate speed updates"""
        chunk_size = 4 * 1024 * 1024  # 4MB chunks
        # Ensure path exists and pre-allocate file size for random access
        with open(path, 'wb') as f:
            f.truncate(total_size)

        chunks_to_download = []
        for start in range(0, total_size, chunk_size):
            end = min(start + chunk_size - 1, total_size - 1)
            chunks_to_download.append((start, end))

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(
                self._download_chunk,
                url, start, end, path, i+1, len(chunks_to_download)
            ): (start, end) for i, (start, end) in enumerate(chunks_to_download)}

            downloaded = 0
            for future in as_completed(futures):
                if not self.active:
                    # If active is False, shutdown all ongoing threads
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.log_signal.emit("Download cancelled during chunk processing.")
                    break
                
                while self.should_pause(): # Pause check during future completion loop
                    time.sleep(0.1)
                    if not self.active: # Re-check active during pause
                        executor.shutdown(wait=False, cancel_futures=True)
                        self.log_signal.emit("Download cancelled during pause.")
                        break
                if not self.active: break # Check again after pause loop

                try:
                    chunk_bytes_downloaded = future.result()
                    downloaded += chunk_bytes_downloaded
                    self._update_speed_metrics(downloaded, total_size)
                    
                except Exception as e:
                    range_info = futures[future]
                    self.log_signal.emit(f"⚠️ Chunk ({range_info[0]}-{range_info[1]}) failed: {str(e)}")
                    # Potentially re-submit failed chunks, or just log error.
                    # For simplicity, we just log and continue for now.
        
        if self.active: # Only if not explicitly stopped
            self._update_speed_metrics(total_size, total_size) # Ensure 100% update

    def _update_speed_metrics(self, downloaded, total_size):
        """Calculate and emit speed/progress updates"""
        now = time.time()
        # Ensure total_paused_time is accurately updated during pause
        elapsed_time = now - self.dl_start_time - self.total_paused_time
        
        if elapsed_time > 0: # Avoid division by zero
            current_speed = (downloaded - self.last_bytes) / (now - self.last_update) if (now - self.last_update) > 0 else 0
            overall_speed = downloaded / elapsed_time
        else:
            current_speed = 0
            overall_speed = 0

        self.speed_signal.emit(overall_speed / (1024 * 1024)) # Convert to MB/s
        self.progress_signal.emit(downloaded, total_size) # Emit raw bytes for ProgressBar
        
        # Log to QTextEdit less frequently for readability
        if now - self.last_update > 5.0: # Update every 5sec for log
            remaining = total_size - downloaded
            eta = remaining / overall_speed if overall_speed > 0 else 0
            
            self.log_signal.emit(
                f"⬇️ Progress: {downloaded / (1024*1024):.1f}/{total_size / (1024*1024):.1f} MB "
                f"Speed: {overall_speed / (1024*1024):.1f} MB/s ETA: {self._format_eta(eta)}"
            )
            self.last_update = now
            self.last_bytes = downloaded

    def _format_eta(self, seconds):
        """Convert seconds to human-readable ETA"""
        if seconds <= 0:
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

    def _download_chunk(self, url, start, end, path, chunk_num, total_chunks):
        """Chunk downloader with detailed logging and retry logic"""
        # self.log_signal.emit(
        #     f"🔽 Starting chunk {chunk_num}/{total_chunks} (Range: {start}-{end} bytes)"
        # ) # Too verbose for general log

        for attempt in range(3):
            try:
                while self.should_pause() and self.active:
                    pause_start = time.time()
                    time.sleep(0.1)
                    self.total_paused_time += (time.time() - pause_start)
                    # Important: if the worker is stopped while paused, break
                    if not self.active: 
                        raise RuntimeError("Download stopped during pause.")

                if not self.active: 
                    raise RuntimeError("Download stopped.")

                headers = HEADERS.copy()
                headers['Range'] = f'bytes={start}-{end}'
                
                response = requests.get(url, headers=headers, stream=True, timeout=15)
                response.raise_for_status()
                
                chunk_data = bytearray()
                for data in response.iter_content(1024 * 256):  # 256KB blocks
                    if not self.active: raise RuntimeError("Download stopped during chunk data reception.")
                    while self.should_pause():
                        pause_start = time.time()
                        time.sleep(0.1)
                        self.total_paused_time += (time.time() - pause_start)
                        if not self.active: raise RuntimeError("Download stopped during pause.")

                    chunk_data.extend(data)
                
                with open(path, 'r+b') as f:
                    f.seek(start)
                    f.write(chunk_data)
                
                # self.log_signal.emit(f"✅ Chunk {chunk_num}/{total_chunks} completed.") # Too verbose
                return len(chunk_data)
                
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    self.log_signal.emit(f"❌ Chunk {chunk_num} failed after 3 attempts due to network error: {str(e)}")
                    raise # Re-raise to be caught by as_completed
                
                self.log_signal.emit(
                    f"🔄 Retrying chunk {chunk_num} (attempt {attempt+1}/3) - Network error: {str(e)}"
                )
                time.sleep(1 + attempt) # Exponential backoff
            except Exception as e:
                if attempt == 2:
                    self.log_signal.emit(f"❌ Chunk {chunk_num} failed after 3 attempts: {str(e)}")
                    raise # Re-raise to be caught by as_completed
                
                self.log_signal.emit(
                    f"🔄 Retrying chunk {chunk_num} (attempt {attempt+1}/3) - Error: {str(e)}"
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

    def _single_thread_download(self, url, path):
        """Fallback single-thread download"""
        downloaded = 0
        start_time = time.time()
        
        with requests.get(url, stream=True, timeout=15) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            
            with open(path, 'wb') as f:
                for data in response.iter_content(1024 * 1024):  # 1MB chunks
                    if not self.active: 
                        self.log_signal.emit("Single-thread download cancelled.")
                        break

                    while self.should_pause():
                        pause_start = time.time()
                        time.sleep(0.1)
                        self.total_paused_time += (time.time() - pause_start)
                        if not self.active: break # Check if stopped while paused

                    f.write(data)
                    downloaded += len(data)
                    
                    self._update_speed_metrics(downloaded, total_size)
            
            if self.active: # Only update to 100% if not cancelled
                self._update_speed_metrics(total_size, total_size)


    def should_pause(self):
        with QtCore.QMutexLocker(self._lock):
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
                # Resolve relative URLs if needed, but often direct download links are absolute
                continue 
            
            # Prioritize links with 'download' attribute
            if a_tag.has_attr('download') or 'download' in link_text or 'get file' in link_text:
                potential_links.append(href)
                
            # Look for common file extensions in the href
            if any(href.endswith(ext) for ext in ['.zip', '.rar', '.exe', '.iso', '.tar.gz', '.torrent', '.dmg']):
                potential_links.append(href)
        
        if potential_links:
            # Simple heuristic: pick the longest URL, often more specific
            return max(potential_links, key=len)

        # 3. Fallback: Check if the original link itself is a direct download or can be simplified
        # (This might be redundant if the initial request gets a file directly)
        if any(original_link.lower().endswith(ext) for ext in ['.zip', '.rar', '.exe', '.iso', '.tar.gz', '.torrent', '.dmg']):
            return original_link

        return None # No suitable download URL found

class QListWidgetLinks(QtWidgets.QListWidget):
    """
    A QListWidget subclass that enables drag-and-drop for URLs
    and provides a context menu for list items.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection) # Allow multi-selection
        self.setToolTip("Drag and drop links here, or double-click to copy.")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            links_added = 0
            for url in event.mimeData().urls():
                if url.scheme() in ('http', 'https'):
                    item_text = url.toString()
                    # Prevent adding duplicates
                    if not any(self.item(i).text().split(". ", 1)[-1] == item_text for i in range(self.count())):
                        self.addItem(item_text)
                        links_added += 1
            if links_added > 0:
                self.parent().parent().log(f"📥 Added {links_added} link(s) via drag & drop.")
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def contextMenuEvent(self, event):
        context_menu = QtWidgets.QMenu(self)
        
        copy_action = context_menu.addAction("Copy Link")
        open_action = context_menu.addAction("Open in Browser")
        remove_action = context_menu.addAction("Remove Selected")
        clear_all_action = context_menu.addAction("Clear All Links")

        action = context_menu.exec_(self.mapToGlobal(event.pos()))

        if action == copy_action:
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


class MainWindow(QtWidgets.QMainWindow):
    """
    Main application window for the downloader.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fucking Fast Downloader")
        self.resize(850, 600)
        self.setStatusBar(QtWidgets.QStatusBar(self))  # For transient notifications

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)

        # Determine base path for resources.
        self.base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))

        try:
            icon_path = os.path.join(self.base_path, "icons", "fuckingfast.ico")
            self.setWindowIcon(QtGui.QIcon(icon_path))
        except Exception as e:
            print(f"Error loading icon: {e}")

        # Set the default application font.
        nice_font = "Roboto" if "Roboto" in QFontDatabase().families() else "Segoe UI"
        QtWidgets.QApplication.setFont(QFont(nice_font, 10))

        # Top buttons.
        top_button_layout = QtWidgets.QHBoxLayout()
        self.load_btn = QtWidgets.QPushButton("📥 Load Links")
        self.load_btn.setToolTip(f"Load links from '{INPUT_FILE}'")
        self.download_btn = QtWidgets.QPushButton("🚀 Download All")
        self.download_btn.setToolTip("Start downloading all links in the list.")
        self.open_downloads_btn = QtWidgets.QPushButton("📂 Open Downloads")
        self.open_downloads_btn.setToolTip(f"Open the '{DOWNLOADS_FOLDER}' folder.")
        
        top_button_layout.addWidget(self.load_btn)
        top_button_layout.addWidget(self.download_btn)
        top_button_layout.addStretch() # Push buttons to left
        top_button_layout.addWidget(self.open_downloads_btn)
        main_layout.addLayout(top_button_layout)

        # Main content layout.
        content_layout = QtWidgets.QHBoxLayout()
        self.list_widget = QListWidgetLinks() # Use custom QListWidgetLinks
        content_layout.addWidget(self.list_widget, 1)
        self.list_widget.itemDoubleClicked.connect(self.copy_link_to_clipboard)

        # Right-side layout for progress and logs.
        right_layout = QtWidgets.QVBoxLayout()
        
        self.file_label = QtWidgets.QLabel("📁 Current File: None")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setFormat("%p% - %v / %m Bytes") # Added percentage

        pause_resume_layout = QtWidgets.QHBoxLayout()
        self.pause_btn = QtWidgets.QPushButton("⏸ Pause")
        self.pause_btn.setObjectName("pause_btn")
        self.pause_btn.setToolTip("Pause current downloads.")
        self.resume_btn = QtWidgets.QPushButton("▶ Resume")
        self.resume_btn.setObjectName("resume_btn")
        self.resume_btn.setToolTip("Resume paused downloads.")
        pause_resume_layout.addWidget(self.pause_btn)
        pause_resume_layout.addWidget(self.resume_btn)

        self.status_label = QtWidgets.QLabel("🟢 Status: Idle")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #4CAF50;")

        self.progress_detail_label = QtWidgets.QLabel(
            "⬇️ Downloaded: 0.00 MB | 📦 Total: 0.00 MB | ⏳ Remaining: 0.00 MB"
        )
        self.progress_detail_label.setStyleSheet("font-weight: 500;")
        self.progress_detail_label.setAlignment(Qt.AlignCenter) # Center align text

        self.speed_label = QtWidgets.QLabel("🚀 Speed: 0.00 KB/s")
        self.speed_label.setStyleSheet("font-weight: 500; color: #FF5722;")
        self.speed_label.setAlignment(Qt.AlignCenter) # Center align text


        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setAcceptRichText(True)
        self.log_text.setFont(QtGui.QFont("Segoe UI", 12))
        
        # Clear log button
        self.clear_log_btn = QtWidgets.QPushButton("🧹 Clear Log")
        self.clear_log_btn.setToolTip("Clear all messages from the log area.")
        self.clear_log_btn.clicked.connect(self.log_text.clear)


        right_layout.addWidget(self.file_label)
        right_layout.addWidget(self.progress_bar)
        right_layout.addLayout(pause_resume_layout)
        right_layout.addWidget(self.status_label) # Moved status here for prominence
        right_layout.addWidget(self.progress_detail_label)
        right_layout.addWidget(self.speed_label)
        right_layout.addWidget(self.log_text, 1) # Takes up remaining space
        right_layout.addWidget(self.clear_log_btn) # Added clear log button
        content_layout.addLayout(right_layout, 2)
        main_layout.addLayout(content_layout)

        # Bottom layout for support buttons.
        self.github_button = QtWidgets.QPushButton()
        github_icon = os.path.join(self.base_path, "icons", "github.png")
        self.github_button.setIcon(QtGui.QIcon(github_icon))
        self.github_button.setIconSize(QtCore.QSize(32, 32)) # Smaller icons
        self.github_button.setToolTip("View Source Code on Github 🐙")
        self.github_button.setStyleSheet("""
            QPushButton {
                border: none;
                margin: 5px; /* Reduced margin */
                padding: 0;
                background-color: transparent;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
        """)
        self.github_button.clicked.connect(
            lambda: webbrowser.open("https://github.com/devbyaryanvala")
        )

        self.buymecoffee_button = QtWidgets.QPushButton()
        buymecoffee_icon = os.path.join(self.base_path, "icons", "buymecoffee.png")
        self.buymecoffee_button.setIcon(QtGui.QIcon(buymecoffee_icon))
        self.buymecoffee_button.setIconSize(QtCore.QSize(32, 32)) # Smaller icons
        self.buymecoffee_button.setToolTip("Just Buy me a Coffee ☕ Already !!")
        self.buymecoffee_button.setStyleSheet("""
            QPushButton {
                border: none;
                margin: 5px; /* Reduced margin */
                padding: 0;
                background-color: transparent;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
        """)
        self.buymecoffee_button.clicked.connect(
            lambda: webbrowser.open("https://www.buymeacoffee.com/yourprofile") # Replace with your actual BMC link
        )

        self.support_label = QtWidgets.QLabel(
            "Check Out What I've Been Up To on Github! 🫡"
        )
        self.support_label.setAlignment(Qt.AlignCenter)
        self.support_label.setStyleSheet("font-size: 12px; font-weight: bold; margin-top: 5px;") # Smaller font/margin

        bottom_layout = QtWidgets.QHBoxLayout()
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.github_button)
        bottom_layout.addWidget(self.buymecoffee_button) # Placed closer
        bottom_layout.addStretch()
        main_layout.addLayout(bottom_layout)
        main_layout.addWidget(self.support_label) # Placed below buttons


        self.credits_label = QtWidgets.QLabel(
            "Made with <span style='color: #FF6347; font-weight: bold;'>❤️</span> by "
            "<a style='color: #1E90FF; text-decoration: none;' href='https://aryanvala.site'>Aryan Vala</a>"
        )
        self.credits_label.setOpenExternalLinks(True)
        self.credits_label.setAlignment(Qt.AlignCenter)
        self.credits_label.setStyleSheet("font-size: 10px; margin-top: 5px;") # Smaller font/margin
        main_layout.addWidget(self.credits_label)

        # Set cursors for interactive elements.
        self.load_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.pause_btn.setCursor(Qt.PointingHandCursor)
        self.resume_btn.setCursor(Qt.PointingHandCursor)
        self.github_button.setCursor(Qt.PointingHandCursor)
        self.buymecoffee_button.setCursor(Qt.PointingHandCursor)
        self.list_widget.setCursor(Qt.ArrowCursor)
        self.open_downloads_btn.setCursor(Qt.PointingHandCursor)
        self.clear_log_btn.setCursor(Qt.PointingHandCursor)


        # Application-wide stylesheet.
        self.setStyleSheet("""
            QPushButton {
                background-color: #2B579A;
                color: white;
                border: 1px solid #1D466B;
                border-radius: 4px;
                padding: 8px 16px;
                margin: 2px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3C6AAA;
                border: 1px solid #2B579A;
            }
            QPushButton:pressed { background-color: #1D466B; }
            QPushButton#pause_btn { background-color: #FF5722; border-color: #CD451F;}
            QPushButton#pause_btn:hover { background-color: #FF7043; }
            QPushButton#resume_btn { background-color: #4CAF50; border-color: #388E3C;}
            QPushButton#resume_btn:hover { background-color: #66BB6A; }
            QListWidget {
                background-color: #1E1E1E;
                color: #FFFFFF;
                border: 1px solid #3C3C3C;
                border-radius: 4px;
                padding: 5px;
            }
            QListWidget::item:hover { background-color: #3C3C3C; }
            QListWidget::item:selected { background-color: #2B579A; }
            QProgressBar {
                border: 1px solid #3C3C3C;
                border-radius: 4px;
                text-align: center;
                color: #FFFFFF; /* Text color on the bar */
                background-color: #1E1E1E;
            }
            QProgressBar::chunk {
                background-color: #2B579A;
                border-radius: 4px;
            }
            QTextEdit {
                background-color: #1E1E1E;
                color: #FFFFFF;
                border: 1px solid #3C3C3C;
                border-radius: 4px;
                padding: 5px;
            }
            QLabel { color: #FFFFFF; }
            QLabel#status_label { /* Targeting by objectName */
                background-color: #282828;
                border-radius: 4px;
                padding: 5px;
                margin-top: 5px;
                margin-bottom: 5px;
                text-align: center;
            }
            QLabel#progress_detail_label, QLabel#speed_label {
                background-color: #282828;
                border-radius: 4px;
                padding: 5px;
                margin-top: 2px;
                margin-bottom: 2px;
            }
        """)

        # Connect button signals.
        self.load_btn.clicked.connect(self.load_links)
        self.download_btn.clicked.connect(self.download_all)
        self.pause_btn.clicked.connect(self.pause_download)
        self.resume_btn.clicked.connect(self.resume_download)
        self.open_downloads_btn.clicked.connect(self.open_downloads_folder)

        self.worker = None
        self.download_queue = [] # Store links to download

        # Initial UI state
        self.update_ui_for_idle()

    def update_ui_for_idle(self):
        self.download_btn.setEnabled(True)
        self.load_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.file_label.setText("📁 Current File: None")
        self.progress_detail_label.setText("⬇️ Downloaded: 0.00 MB | 📦 Total: 0.00 MB | ⏳ Remaining: 0.00 MB")
        self.speed_label.setText("🚀 Speed: 0.00 KB/s")
        self.status_label.setText("🟢 Status: Idle")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #4CAF50;") # Green for idle

    def update_ui_for_downloading(self):
        self.download_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False) # Only enable resume if paused

    def update_ui_for_paused(self):
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)

    def update_ui_for_resumed(self):
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)

    def load_links(self):
        if not os.path.exists(INPUT_FILE):
            with open(INPUT_FILE, 'w') as f:
                f.write("# Add download links here (remove this line and add links only)\n")
            QtWidgets.QMessageBox.information(self, "Info", f"Input file '{INPUT_FILE}' not found. It has been created. Please add links and reload.")
            return

        self.list_widget.clear()
        self.download_queue.clear()
        with open(INPUT_FILE, 'r') as f:
            for line in f:
                stripped_line = line.strip()
                if stripped_line and not stripped_line.startswith("#"):
                    # Add to QListWidget and internal queue
                    self.list_widget.addItem(stripped_line)
                    self.download_queue.append(stripped_line)
        self.log(f"📥 Loaded {len(self.download_queue)} link(s) from {INPUT_FILE}")
        self.update_link_numbers() # Re-number after load
        self.update_ui_for_idle()

    def copy_link_to_clipboard(self, item):
        link = item.text().split(". ", 1)[-1] if ". " in item.text() else item.text()
        QtWidgets.QApplication.clipboard().setText(link)
        self.statusBar().showMessage("Link copied to clipboard", 2000)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        colored_message = colorize_log_message(message)
        self.log_text.append(f"<p style='font-weight:600; font-family: \"Segoe UI\"; font-size:12px;'><span style='color:gray;'>[{timestamp}]</span> {colored_message}</p>")
        # Auto-scroll to bottom
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())


    def download_all(self):
        # Stop any existing worker if active
        if self.worker and self.worker.isRunning():
            self.log("🛑 Stopping current download session before starting new one...")
            self.worker.stop()
            self.worker.wait(3000) # Wait for it to terminate

        if not self.download_queue:
            QtWidgets.QMessageBox.information(self, "Info", "No links to download. Please load links first.")
            return
        
        # Reset progress for new session
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.file_label.setText("📁 Current File: None")
        self.progress_detail_label.setText("⬇️ Downloaded: 0.00 MB | 📦 Total: 0.00 MB | ⏳ Remaining: 0.00 MB")
        self.speed_label.setText("🚀 Speed: 0.00 KB/s")

        self.worker = DownloaderWorker(self.download_queue[:]) # Pass a copy of the queue
        self.worker.log_signal.connect(self.log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.file_signal.connect(self.update_file)
        self.worker.status_signal.connect(self.update_status)
        self.worker.speed_signal.connect(self.update_speed)
        self.worker.link_completed_signal.connect(self.handle_link_completed)
        self.worker.link_failed_signal.connect(self.handle_link_failed)
        self.worker.session_finished_signal.connect(self.update_ui_for_idle)
        
        self.worker.start()
        self.update_ui_for_downloading()
        self.log("🚀 Download session initiated.")

    def pause_download(self):
        if self.worker and self.worker.isRunning():
            self.worker.pause()
            self.update_ui_for_paused()

    def resume_download(self):
        if self.worker and self.worker.isRunning():
            self.worker.resume_download()
            self.update_ui_for_resumed()

    def open_downloads_folder(self):
        """Opens the downloads folder using the OS default file manager."""
        if not os.path.exists(DOWNLOADS_FOLDER):
            os.makedirs(DOWNLOADS_FOLDER) # Ensure it exists
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(DOWNLOADS_FOLDER)))
        self.log(f"📂 Opened downloads folder: {os.path.abspath(DOWNLOADS_FOLDER)}")

    def update_progress(self, downloaded, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(downloaded)
        
        downloaded_mb = downloaded / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        remaining_mb = max(total_mb - downloaded_mb, 0)

        # Update format for progress bar text
        percent = (downloaded / total) * 100 if total > 0 else 0
        self.progress_bar.setFormat(f"{percent:.1f}% - {downloaded_mb:.2f}/{total_mb:.2f} MB")

        self.progress_detail_label.setText(
            f"⬇️ Downloaded: {downloaded_mb:.2f} MB | "
            f"📦 Total: {total_mb:.2f} MB | "
            f"⏳ Remaining: {remaining_mb:.2f} MB"
        )

    def handle_critical_error(self, message):
        QtWidgets.QMessageBox.critical(
            self, 
            "Critical Error", 
            f"Application will stop:\n{message}"
        )
        if self.worker:
            self.worker.stop()
        self.update_ui_for_idle() # Revert UI to idle state

    def closeEvent(self, event):
        """Cleanup on window close"""
        if self.worker and self.worker.isRunning():
            self.log("🛑 Stopping download worker on application exit...")
            self.worker.stop() # Signal worker to stop
            self.worker.wait(5000) # Give it time to finish
        event.accept()

    def update_file(self, filename):
        self.file_label.setText(f"📁 Current File: {filename}")

    def update_status(self, status):
        self.status_label.setText(f"🟢 Status: {status}")
        # Dynamic color change for status
        if "Paused" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FFD700;") # Gold
        elif "Downloading" in status or "Resuming" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #1E90FF;") # DodgerBlue
        elif "Idle" in status or "Completed" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #4CAF50;") # LimeGreen
        elif "Error" in status or "Failed" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FF6347;") # Tomato


    def update_speed(self, speed_mb):
        """Handle speed updates with proper unit formatting"""
        if speed_mb >= 1.0:
            self.speed_label.setText(f"🚀 Speed: {speed_mb:.2f} MB/s")
        else:
            speed_kb = speed_mb * 1024
            self.speed_label.setText(f"🚀 Speed: {speed_kb:.1f} KB/s")

    def update_link_numbers(self):
        """Re-numbers the items in the QListWidget based on their current index."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            original_link = item.text().split(". ", 1)[-1] if ". " in item.text() else item.text()
            # Preserve existing color/font
            current_color = item.foreground()
            current_font = item.font()
            item.setText(f"{i + 1}. {original_link}")
            item.setForeground(current_color)
            item.setFont(current_font)


    def handle_link_completed(self, link_completed):
        """
        Handles a link that has successfully completed download.
        Removes it from the QListWidget, updates input.txt, and re-numbers.
        """
        # Remove from QListWidget
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            # Extract the actual link from the item text (e.g., "1. http://link.com" -> "http://link.com")
            current_link_in_list = item.text().split(". ", 1)[-1] 
            if current_link_in_list == link_completed:
                self.list_widget.takeItem(i)
                self.log(f"🗑️ Removed completed link '{link_completed[:50]}...' from list.")
                break
        
        # Remove from internal download_queue (so it's not processed again if 'Download All' clicked)
        if link_completed in self.download_queue:
            self.download_queue.remove(link_completed)

        # Update input.txt file
        self._update_input_file()
        self.update_link_numbers() # Re-number the list after removal

    def handle_link_failed(self, failed_link, error_message):
        """
        Marks a link in the list widget as failed (red color) and logs the error.
        Does NOT remove it from the list or input.txt, allowing retry.
        """
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            current_link_in_list = item.text().split(". ", 1)[-1]
            if current_link_in_list == failed_link:
                item.setForeground(QtGui.QColor("red"))
                item.setToolTip(f"Failed: {error_message}")
                self.log(f"❌ Link '{failed_link[:50]}...' failed: {error_message}")
                break
        self.update_status("⚠️ Error: Check Log")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FF6347;") # Red for error

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
            links_to_remove_from_queue = []
            # Remove from list widget in reverse order to avoid index issues
            for item in reversed(selected_items):
                row = self.list_widget.row(item)
                link_to_remove = item.text().split(". ", 1)[-1]
                self.list_widget.takeItem(row)
                links_to_remove_from_queue.append(link_to_remove)
                self.log(f"🗑️ Removed '{link_to_remove[:50]}...' from list.")

            # Update internal download_queue
            self.download_queue = [link for link in self.download_queue if link not in links_to_remove_from_queue]
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
            self.log("🗑️ All links cleared from list and input.txt.")
            self.update_ui_for_idle()

    def _update_input_file(self):
        """Rewrites the input.txt file with the current links in the download queue."""
        try:
            with open(INPUT_FILE, 'w') as f:
                f.write("# Add download links here (lines starting with # are comments)\n")
                for link in self.download_queue:
                    f.write(link + "\n")
            self.log(f"📝 {INPUT_FILE} updated successfully.")
        except Exception as e:
            self.log(f"❌ Error writing to {INPUT_FILE}: {e}")


# --------------------- End of GUI Code ---------------------

def main():
    app = QtWidgets.QApplication(sys.argv)
    default_font = QFont("Roboto" if "Roboto" in QFontDatabase().families() else "Segoe UI", 10)
    app.setFont(default_font)
    apply_stylesheet(app, theme='dark_blue.xml') # Consider other themes like 'light_blue.xml', 'dark_amber.xml'
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()