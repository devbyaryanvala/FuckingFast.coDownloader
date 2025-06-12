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
from PyQt5.QtCore import Qt, QUrl, QThread, pyqtSignal, QMutex, QMutexLocker
from PyQt5.QtGui import QFont, QFontDatabase, QDesktopServices, QColor # Import QFontDatabase
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
        self.setToolTip("Drag and drop links here, or double-click to copy. Right-click for more options.")

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
                    # Prevent adding duplicates by checking the link part after numbering
                    if not any(self.item(i).text().split(". ", 1)[-1] == item_text for i in range(self.count())):
                        self.addItem(item_text) # Add item directly, main window will re-number later
                        # Also add to the main window's download queue
                        self.parent().parent().download_queue.append(item_text)
                        links_added += 1
            if links_added > 0:
                self.parent().parent().log(f"📥 Added {links_added} link(s) via drag & drop.")
                self.parent().parent().update_link_numbers() # Re-number after drops
                self.parent().parent()._update_input_file() # Save to file immediately
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


class AddLinkDialog(QtWidgets.QDialog):
    """
    A simple dialog for adding a single download link manually.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Link")
        self.setFixedSize(400, 100)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint) # Remove help button

        layout = QtWidgets.QVBoxLayout(self)

        self.link_input = QtWidgets.QLineEdit()
        self.link_input.setPlaceholderText("Paste your download link here...")
        layout.addWidget(self.link_input)

        add_button = QtWidgets.QPushButton("Add Link")
        add_button.clicked.connect(self.accept)
        layout.addWidget(add_button)
        
        self.link_input.returnPressed.connect(self.accept) # Allow pressing Enter to add

    def get_link(self):
        return self.link_input.text().strip()


class MainWindow(QtWidgets.QMainWindow):
    """
    Main application window for the downloader.
    """
    THEMES = {
        "Dark Blue": "dark_blue.xml",
        "Light Blue": "light_blue.xml",
        "Dark Amber": "dark_amber.xml",
        "Light Amber": "light_amber.xml",
        "Dark Green": "dark_green.xml",
        "Light Green": "light_green.xml",
        "Dark Purple": "dark_purple.xml",
        "Light Purple": "light_purple.xml",
        "Dark Red": "dark_red.xml",
        "Light Red": "light_red.xml",
        "Dark Teal": "dark_teal.xml",
        "Light Teal": "light_teal.xml",
        "Dark Cyan": "dark_cyan.xml",
        "Light Cyan": "light_cyan.xml",
        "Dark Grey": "dark_grey.xml",
        "Light Grey": "light_grey.xml",
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Zuiz Downloader")
        self.resize(1000, 700) # Increased default size
        self.setStatusBar(QtWidgets.QStatusBar(self))  # For transient notifications

        self.base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))

        try:
            # Assuming 'logo.png' is in 'icons' folder next to the script/executable
            icon_path = os.path.join(self.base_path, "icons", "logo.png")
            if os.path.exists(icon_path):
                self.setWindowIcon(QtGui.QIcon(icon_path))
            else:
                raise FileNotFoundError(f"Icon not found at {icon_path}")
        except Exception as e:
            print(f"Error loading icon: {e}")
            # Fallback to a generic icon if qtawesome is available
            if qta:
                self.setWindowIcon(qta.icon('fa5s.download'))
            else:
                print("qtawesome not installed and custom icon not found. No icon will be displayed.")

        # Set the default application font.
        nice_font = "Roboto" if "Roboto" in QFontDatabase().families() else "Segoe UI"
        QtWidgets.QApplication.setFont(QFont(nice_font, 10))

        # --- Instantiate ALL UI Widgets FIRST ---
        # This is crucial to avoid AttributeError when applying styles or connecting signals early.
        self.log_text = QtWidgets.QTextEdit()
        self.clear_log_btn = QtWidgets.QPushButton("Clear Log")
        self.load_btn = QtWidgets.QPushButton("Load Links")
        self.open_downloads_btn = QtWidgets.QPushButton("Open Downloads Folder")
        self.download_btn = QtWidgets.QPushButton("Download All")
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.resume_btn = QtWidgets.QPushButton("Resume")
        self.stop_btn = QtWidgets.QPushButton("Stop All")
        self.theme_combo = QtWidgets.QComboBox()
        self.github_button = QtWidgets.QPushButton("GitHub")
        self.buymecoffee_button = QtWidgets.QPushButton("Buy Me a Coffee")
        self.support_label = QtWidgets.QLabel("Check Out What I've Been Up To! 🫡")
        self.credits_label = QtWidgets.QLabel("") # Initialized empty, text set later
        self.add_link_btn = QtWidgets.QPushButton("Add Link Manually")
        self.link_count_label = QtWidgets.QLabel("Total Links: 0")
        self.list_widget = QListWidgetLinks()
        self.file_label = QtWidgets.QLabel("Current File: None")
        self.progress_bar = QtWidgets.QProgressBar()
        self.status_label = QtWidgets.QLabel("🟢 Idle")
        self.progress_detail_label = QtWidgets.QLabel("Downloaded: 0.00 MB | Total: 0.00 MB")
        self.speed_label = QtWidgets.QLabel("Speed: 0.00 KB/s")
        self.eta_label = QtWidgets.QLabel("ETA: N/A")

        # --- Apply initial theme and load settings ---
        self.load_settings()
        # Changed default theme to 'dark_teal.xml' for better aesthetics
        apply_stylesheet(self.window(), theme=self.settings.get('theme', 'dark_teal.xml'))
        # Call apply_custom_styles here immediately after applying the qt_material stylesheet
        self.apply_custom_styles()


        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central) 

        # --- Log Area Setup (Moved up for early instantiation) ---
        log_group = QtWidgets.QGroupBox("Logs")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_text.setReadOnly(True)
        self.log_text.setAcceptRichText(True)
        self.log_text.setFont(QtGui.QFont(nice_font, 9))
        log_layout.addWidget(self.log_text)
        
        self.clear_log_btn.clicked.connect(self.log_text.clear)
        if qta:
            self.clear_log_btn.setIcon(qta.icon('fa5s.eraser'))
        log_layout.addWidget(self.clear_log_btn)
        # End of Log Area Setup

        # --- Left Sidebar for Controls ---
        sidebar_layout = QtWidgets.QVBoxLayout()
        sidebar_layout.setAlignment(Qt.AlignTop)

        # Logo/Title
        logo_label = QtWidgets.QLabel("<h1>fuckingfast.co Downloader</h1>")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("margin-bottom: 20px; color: #40E0D0;")
        sidebar_layout.addWidget(logo_label)

        # File Operations Group
        file_ops_group = QtWidgets.QGroupBox("File Operations")
        file_ops_layout = QtWidgets.QVBoxLayout(file_ops_group)
        self.load_btn.setToolTip(f"Load links from '{INPUT_FILE}'")
        self.open_downloads_btn.setToolTip(f"Open the '{DOWNLOADS_FOLDER}' folder.")
        
        if qta:
            self.load_btn.setIcon(qta.icon('fa5s.file-import'))
            self.open_downloads_btn.setIcon(qta.icon('fa5s.folder-open'))
        
        file_ops_layout.addWidget(self.load_btn)
        file_ops_layout.addWidget(self.open_downloads_btn)
        sidebar_layout.addWidget(file_ops_group)
        sidebar_layout.addSpacing(15)

        # Download Controls Group
        download_controls_group = QtWidgets.QGroupBox("Download Controls")
        download_controls_layout = QtWidgets.QVBoxLayout(download_controls_group)
        self.download_btn.setToolTip("Start downloading all links in the list.")
        self.pause_btn.setObjectName("pause_btn")
        self.pause_btn.setToolTip("Pause current downloads.")
        self.resume_btn.setObjectName("resume_btn")
        self.resume_btn.setToolTip("Resume paused downloads.")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setToolTip("Stop all ongoing downloads and reset.")

        if qta:
            self.download_btn.setIcon(qta.icon('fa5s.play'))
            self.pause_btn.setIcon(qta.icon('fa5s.pause'))
            self.resume_btn.setIcon(qta.icon('fa5s.forward'))
            self.stop_btn.setIcon(qta.icon('fa5s.stop'))

        download_controls_layout.addWidget(self.download_btn)
        download_controls_layout.addWidget(self.pause_btn)
        download_controls_layout.addWidget(self.resume_btn)
        download_controls_layout.addWidget(self.stop_btn)
        sidebar_layout.addWidget(download_controls_group)
        sidebar_layout.addSpacing(15)

        # Theme Selector
        theme_group = QtWidgets.QGroupBox("Theme Settings")
        theme_layout = QtWidgets.QVBoxLayout(theme_group)
        theme_label = QtWidgets.QLabel("Select Application Theme:")
        theme_label.setStyleSheet("font-weight: bold; color: #BBBBBB;")
        theme_layout.addWidget(theme_label)
        self.theme_combo.addItems(sorted(self.THEMES.keys()))
        self.theme_combo.currentIndexChanged.connect(self.change_theme)
        
        # Set initial theme selection in combo box
        current_theme_file = self.settings.get('theme', 'dark_teal.xml')
        for name, file in self.THEMES.items():
            if file == current_theme_file:
                self.theme_combo.setCurrentText(name)
                break

        theme_layout.addWidget(self.theme_combo)
        sidebar_layout.addWidget(theme_group)
        sidebar_layout.addStretch()

        # Bottom support/credits in sidebar
        if qta:
            self.github_button.setIcon(qta.icon('fa5b.github', color='white'))
        else:
            github_icon_path = os.path.join(self.base_path, "icons", "github.png")
            if os.path.exists(github_icon_path):
                self.github_button.setIcon(QtGui.QIcon(github_icon_path))
        self.github_button.setIconSize(QtCore.QSize(20, 20))
        self.github_button.setToolTip("View Source Code on Github 🐙")
        self.github_button.clicked.connect(lambda: webbrowser.open("https://github.com/devbyaryanvala"))
        self.github_button.setStyleSheet("QPushButton { text-align: left; padding: 5px; border-radius: 5px; background-color: #383838; margin-top: 10px; border: none;} QPushButton:hover { background-color: #4A4A4A; }")
        

        if qta:
            self.buymecoffee_button.setIcon(qta.icon('fa5s.coffee', color='#FFDD00'))
        else:
            buymecoffee_icon_path = os.path.join(self.base_path, "icons", "buymecoffee.png")
            if os.path.exists(buymecoffee_icon_path):
                self.buymecoffee_button.setIcon(QtGui.QIcon(buymecoffee_icon_path))
        self.buymecoffee_button.setIconSize(QtCore.QSize(20, 20))
        self.buymecoffee_button.setToolTip("Just Buy me a Coffee ☕ Already !!")
        self.buymecoffee_button.clicked.connect(lambda: webbrowser.open("https://www.buymeacoffee.com/yourprofile"))
        self.buymecoffee_button.setStyleSheet("QPushButton { text-align: left; padding: 5px; border-radius: 5px; background-color: #383838; margin-bottom: 10px; border: none;} QPushButton:hover { background-color: #4A4A4A; }")

        sidebar_layout.addWidget(self.github_button)
        # sidebar_layout.addWidget(self.buymecoffee_button)
        
        self.support_label.setAlignment(Qt.AlignCenter)
        self.support_label.setStyleSheet("font-size: 10px; font-weight: bold; color: #BBBBBB;")
        sidebar_layout.addWidget(self.support_label)

        self.credits_label.setText(
            "Made with <span style='color: #FF6347; font-weight: bold;'>❤️</span> by "
            "<a style='color: #40E0D0; text-decoration: none;' href='https://aryanvala.site'>Aryan Vala</a>"
        )
        self.credits_label.setOpenExternalLinks(True)
        self.credits_label.setAlignment(Qt.AlignCenter)
        self.credits_label.setStyleSheet("font-size: 9px; margin-top: 5px; margin-bottom: 10px;")
        sidebar_layout.addWidget(self.credits_label)
        
        main_layout.addLayout(sidebar_layout, 1)


        # --- Main Content Area (Link List + Download Details) ---
        content_area_layout = QtWidgets.QVBoxLayout()

        # Link List with Count
        link_list_group = QtWidgets.QGroupBox("Download Queue")
        link_list_layout = QtWidgets.QVBoxLayout(link_list_group)
        
        link_list_header_layout = QtWidgets.QHBoxLayout()
        self.add_link_btn.setToolTip("Add a single download link to the queue.")
        if qta:
            self.add_link_btn.setIcon(qta.icon('fa5s.plus'))
        self.add_link_btn.clicked.connect(self.add_single_link)
        link_list_header_layout.addWidget(self.add_link_btn)

        self.link_count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.link_count_label.setStyleSheet("font-weight: bold; color: #BBBBBB; padding-right: 5px;")
        link_list_header_layout.addWidget(self.link_count_label)
        link_list_layout.addLayout(link_list_header_layout)

        self.list_widget.itemDoubleClicked.connect(self.copy_link_to_clipboard)
        self.list_widget.model().rowsMoved.connect(self.update_link_numbers)
        
        link_list_layout.addWidget(self.list_widget)
        content_area_layout.addWidget(link_list_group, 2)


        # Download Progress & Status Group
        download_info_group = QtWidgets.QGroupBox("Current Download")
        download_info_layout = QtWidgets.QFormLayout(download_info_group)

        self.file_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #40E0D0;")
        download_info_layout.addRow("📁 File:", self.file_label)

        self.progress_bar.setFormat("%p% - %v / %m")
        download_info_layout.addRow("Progress:", self.progress_bar)

        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #4CAF50;")
        self.status_label.setAlignment(Qt.AlignCenter)
        download_info_layout.addRow("Status:", self.status_label)

        self.progress_detail_label.setStyleSheet("font-weight: 500; color: #C0C0C0;")
        self.progress_detail_label.setAlignment(Qt.AlignCenter)
        download_info_layout.addRow("Details:", self.progress_detail_label)

        self.speed_label.setStyleSheet("font-weight: 500; color: #FF5722;")
        self.speed_label.setAlignment(Qt.AlignCenter)
        download_info_layout.addRow("Speed:", self.speed_label)
        
        self.eta_label.setStyleSheet("font-weight: 500; color: #FFD700;")
        self.eta_label.setAlignment(Qt.AlignCenter)
        download_info_layout.addRow("ETA:", self.eta_label)

        content_area_layout.addWidget(download_info_group, 1)


        # Log Area (Already instantiated and set up above)
        content_area_layout.addWidget(log_group, 1)

        main_layout.addLayout(content_area_layout, 3)


        # Set cursors for interactive elements.
        self.load_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.pause_btn.setCursor(Qt.PointingHandCursor)
        self.resume_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.github_button.setCursor(Qt.PointingHandCursor)
        self.buymecoffee_button.setCursor(Qt.PointingHandCursor)
        self.list_widget.setCursor(Qt.ArrowCursor)
        self.open_downloads_btn.setCursor(Qt.PointingHandCursor)
        self.clear_log_btn.setCursor(Qt.PointingHandCursor)
        self.theme_combo.setCursor(Qt.PointingHandCursor)
        self.add_link_btn.setCursor(Qt.PointingHandCursor)

        # Application-wide stylesheet.
        # This global stylesheet should be applied only once after qt_material
        # and then apply_custom_styles will handle specific overrides.
        self.setStyleSheet("""
            QMainWindow {
                background-color: #212121;
            }
            QGroupBox {
                border: 1px solid #3A3A3A;
                border-radius: 8px; 
                margin-top: 1ex;
                font-weight: bold;
                /* color is set dynamically via apply_custom_styles */
                background-color: #2C2C2C;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 8px; 
                background-color: #2C2C2C;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #444444;
                border: 1px solid #555555;
                color: #E0E0E0;
                padding: 10px 15px; 
                border-radius: 5px;
                font-weight: bold;
                font-size: 11px;
                margin-top: 5px;
                margin-bottom: 5px;
                outline: none;
            }
            QPushButton:hover {
                background-color: #555555; 
                border-color: #666666;
            }
            QPushButton:pressed {
                background-color: #333333; 
                border-color: #444444;
            }
            QPushButton:disabled {
                background-color: #222222;
                border-color: #333333;
                color: #777777;
            }

            /* Specific button styles */
            QPushButton#pause_btn { background-color: #D35400; border-color: #A04000;}
            QPushButton#pause_btn:hover { background-color: #E67E22; }
            QPushButton#resume_btn { background-color: #27AE60; border-color: #1F8B4C;}
            QPushButton#resume_btn:hover { background-color: #2ECC71; }
            QPushButton#stop_btn { background-color: #6C7A89; border-color: #5E6977;}
            QPushButton#stop_btn:hover { background-color: #83919F; }

            QListWidget {
                background-color: #1A1A1A;
                color: #E0E0E0;
                border: 1px solid #3A3A3A;
                border-radius: 8px; 
                padding: 10px; 
                show-decoration-selected: 1;
            }
            QListWidget::item {
                padding: 5px; 
                margin: 2px 0;
            }
            QListWidget::item:hover { 
                background-color: #3A3A3A; 
                border-radius: 4px;
            }
            QListWidget::item:selected { 
                background-color: #008CBA;
                border-radius: 4px; 
                color: white;
            }
            
            QProgressBar {
                border: 1px solid #3A3A3A;
                border-radius: 8px;
                text-align: center;
                color: #FFFFFF;
                background-color: #1A1A1A;
                height: 28px;
                font-weight: bold;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background-color: #00A388;
                border-radius: 7px; 
            }
            QTextEdit {
                background-color: #1A1A1A;
                color: #C0C0C0;
                border: 1px solid #3A3A3A;
                border-radius: 8px;
                padding: 10px;
            }
            QLabel { color: #E0E0E0; }
            QLabel#status_label, QLabel#progress_detail_label, QLabel#speed_label, QLabel#eta_label {
                background-color: #212121;
                border-radius: 6px; 
                padding: 8px;
                margin-top: 4px;
                margin-bottom: 4px;
                text-align: center;
                border: 1px solid #3A3A3A;
            }
            QLineEdit {
                border: 1px solid #3A3A3A;
                border-radius: 5px;
                padding: 5px;
                background-color: #1A1A1A;
                color: #E0E0E0;
            }
            QComboBox {
                border: 1px solid #3A3A3A;
                border-radius: 5px;
                padding: 5px;
                background-color: #1A1A1A;
                color: #E0E0E0;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 25px;
                border-left-width: 1px;
                border-left-color: #3A3A3A;
                border-left-style: solid;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }
        """)

        # Connect button signals.
        self.load_btn.clicked.connect(self.load_links)
        self.download_btn.clicked.connect(self.download_all)
        self.pause_btn.clicked.connect(self.pause_download)
        self.resume_btn.clicked.connect(self.resume_download)
        self.stop_btn.clicked.connect(self.stop_download)
        self.open_downloads_btn.clicked.connect(self.open_downloads_folder)
        self.add_link_btn.clicked.connect(self.add_single_link) # Connect add link button

        self.worker = None
        self.download_queue = [] # Store links to download
        self.successful_downloads = []
        self.failed_downloads = []

        # Initial UI state
        self.update_ui_for_idle()

    def load_settings(self):
        """Loads application settings from a JSON file."""
        self.settings = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    self.settings = json.load(f)
            except json.JSONDecodeError as e:
                # Log to console if log_text is not yet available
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error reading config file: {e}. Using default settings.")
        
    def save_settings(self):
        """Saves current application settings to a JSON file."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            self.log(f"❌ Error saving config file: {e}")

    def update_ui_for_idle(self):
        self.download_btn.setEnabled(True)
        self.load_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.add_link_btn.setEnabled(True) # Enable add link when idle
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.file_label.setText("Current File: None")
        self.progress_detail_label.setText("Downloaded: 0.00 MB | Total: 0.00 MB")
        self.speed_label.setText("Speed: 0.00 KB/s")
        self.eta_label.setText("ETA: N/A")
        self.status_label.setText("🟢 Idle")
        # Ensure consistency with improved theme colors
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #27AE60;") # Darker green for idle
        self.update_link_numbers() # Update count on idle

    def update_ui_for_downloading(self):
        self.download_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)  
        self.stop_btn.setEnabled(True)
        self.add_link_btn.setEnabled(False) # Disable add link while downloading

    def update_ui_for_paused(self):
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)

    def update_ui_for_resumed(self):
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def change_theme(self, index):
        theme_name = self.theme_combo.currentText()
        theme_file = self.THEMES.get(theme_name, "dark_teal.xml") # Updated default
        apply_stylesheet(self.window(), theme=theme_file)
        self.settings['theme'] = theme_file # Save theme to settings
        self.save_settings()
        self.log(f"🎨 Theme changed to '{theme_name}'.")
        # Reapply specific stylesheet parts that might be overridden by apply_stylesheet
        self.apply_custom_styles()

    def apply_custom_styles(self):
        """
        Applies custom CSS styles to specific widgets.
        Called after apply_stylesheet to ensure these overrides take effect.
        """
        self.file_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #40E0D0;")
        self.progress_detail_label.setStyleSheet("font-weight: 500; color: #C0C0C0;")
        self.speed_label.setStyleSheet("font-weight: 500; color: #FF5722;")
        self.eta_label.setStyleSheet("font-weight: 500; color: #FFD700;")
        
        # We need to re-apply the correct status color when setting custom styles
        # or it will revert to the default theme color for QLabels.
        # So, we check the current status and set the color accordingly.
        current_status_text = self.status_label.text()
        if "Paused" in current_status_text:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FFD700;")
        elif "Downloading" in current_status_text or "Resuming" in current_status_text or "Fetching" in current_status_text:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #1E90FF;")
        elif "Idle" in current_status_text or "Finished" in current_status_text or "Completed" in current_status_text:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #27AE60;")
        elif "Error" in current_status_text or "Failed" in current_status_text or "Stopping" in current_status_text:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FF6347;")


        # Apply specific colors to groupbox titles (need to access the actual QGroupBox objects)
        # Assuming we named the QGroupBoxes or can reliably find them.
        # This part requires the QGroupBoxes to be instantiated first.
        # The easiest way is to set their style directly during their creation,
        # or after their creation and before being added to layouts.
        # For simplicity here, I'll rely on the global stylesheet for QGroupBoxes
        # and manage dynamic colors for specific labels.
        # If you need specific groupbox titles to have colors that override theme, you'd add:
        # self.file_ops_group.setStyleSheet("QGroupBox::title { color: #40E0D0; }")
        # You'd need to save references to these groupbox objects.

        # Let's ensure group box titles also get the accent color, by setting their object names
        # and using them in the stylesheet if needed, or explicitly here.
        # Given the previous approach of setting them as `QGroupBox("Title")`,
        # we need to reference them if we want to change their stylesheet directly.
        # A cleaner way is to make sure your global stylesheet handles them properly
        # or pass `self.group_box_name` to layouts to retain a reference.
        # For now, I'll update the main stylesheet to assume the default QGroupBox::title styling.

        # If you need to explicitly set group box title color here, ensure you capture the group box widgets:
        # self.file_ops_group = QtWidgets.QGroupBox("File Operations")
        # self.download_controls_group = QtWidgets.QGroupBox("Download Controls")
        # ... and then in this function:
        # self.file_ops_group.setStyleSheet("QGroupBox { color: #40E0D0; }") 
        # (similar for others)
        pass # The global stylesheet handles QGroupBox titles now, removed explicit calls here

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
                    # Add to QListWidget and internal queue
                    self.list_widget.addItem(stripped_line)
                    self.download_queue.append(stripped_line)
        self.log(f"📥 Loaded {len(self.download_queue)} link(s) from {INPUT_FILE}")
        self.update_link_numbers() # Re-number and update count after load
        self.update_ui_for_idle()

    def add_single_link(self):
        """Opens a dialog to add a single link manually."""
        dialog = AddLinkDialog(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            link = dialog.get_link()
            if link:
                if not QUrl(link).isValid() or not QUrl(link).scheme() in ('http', 'https'):
                    QtWidgets.QMessageBox.warning(self, "Invalid URL", "Please enter a valid HTTP or HTTPS URL.")
                    return
                
                # Prevent adding duplicates
                if link not in self.download_queue:
                    self.list_widget.addItem(link)
                    self.download_queue.append(link)
                    self.update_link_numbers()
                    self._update_input_file()
                    self.log(f"➕ Added new link: {link[:50]}...")
                else:
                    self.statusBar().showMessage("Link already in queue.", 2000)
            else:
                self.statusBar().showMessage("No link entered.", 2000)

    def copy_link_to_clipboard(self, item):
        link = item.text().split(". ", 1)[-1] if ". " in item.text() else item.text()
        QtWidgets.QApplication.clipboard().setText(link)
        self.statusBar().showMessage("Link copied to clipboard", 2000)

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        colored_message = colorize_log_message(message)
        # Ensure self.log_text exists before appending to it
        if hasattr(self, 'log_text'):
            self.log_text.append(f"<p style='font-weight:600; font-family: \"{QtWidgets.QApplication.font().family()}\"; font-size:{QtWidgets.QApplication.font().pointSize()-1}pt;'><span style='color:gray;'>[{timestamp}]</span> {colored_message}</p>")
            # Auto-scroll to bottom
            self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
        else:
            print(f"[{timestamp}] {message}") # Fallback to console print if log_text not initialized

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
        self.file_label.setText("Current File: None")
        self.progress_detail_label.setText("Downloaded: 0.00 MB | Total: 0.00 MB")
        self.speed_label.setText("Speed: 0.00 KB/s")
        self.eta_label.setText("ETA: N/A")
        self.successful_downloads = []
        self.failed_downloads = []

        self.worker = DownloaderWorker(self.download_queue[:]) # Pass a copy of the queue
        self.worker.log_signal.connect(self.log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.file_signal.connect(self.update_file)
        self.worker.status_signal.connect(self.update_status)
        self.worker.speed_signal.connect(self.update_speed)
        self.worker.link_completed_signal.connect(self.handle_link_completed)
        self.worker.link_failed_signal.connect(self.handle_link_failed)
        self.worker.session_finished_signal.connect(self.handle_session_finished)
        self.worker.link_processing_signal.connect(self.mark_link_processing) # Connect new signal
        
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

    def stop_download(self):
        if self.worker and self.worker.isRunning():
            self.log("🛑 Requesting worker to stop...")
            self.worker.stop()
            self.update_status("Stopping...")
            self.statusBar().showMessage("Download stopping...", 3000)
        else:
            self.log("No active download to stop.")
            self.update_ui_for_idle()

    def open_downloads_folder(self):
        """Opens the downloads folder using the OS default file manager."""
        if not os.path.exists(DOWNLOADS_FOLDER):
            os.makedirs(DOWNLOADS_FOLDER) # Ensure it exists
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(DOWNLOADS_FOLDER)))
        self.log(f"📂 Opened downloads folder: {os.path.abspath(DOWNLOADS_FOLDER)}")

    def update_progress(self, downloaded, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(downloaded)
        
        # Display progress as percentage and then MB/GB
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

    def handle_critical_error(self, message):
        QtWidgets.QMessageBox.critical(
            self, 
            "Critical Error", 
            f"Application will stop:\n{message}"
        )
        if self.worker:
            self.worker.stop()
        self.update_ui_for_idle()

    def closeEvent(self, event):
        """Cleanup on window close"""
        if self.worker and self.worker.isRunning():
            self.log("🛑 Stopping download worker on application exit...")
            self.worker.stop() # Signal worker to stop
            self.worker.wait(5000) # Give it time to finish
        self.save_settings() # Save settings on close
        event.accept()

    def update_file(self, filename):
        self.file_label.setText(f"Current File: {filename}")

    def update_status(self, status):
        self.status_label.setText(f"🟢 {status}")
        # Dynamic color change for status
        if "Paused" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FFD700;") # Gold
        elif "Downloading" in status or "Resuming" in status or "Fetching" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #1E90FF;") # DodgerBlue
        elif "Idle" in status or "Finished" in status or "Completed" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #27AE60;") # Matching new green
        elif "Error" in status or "Failed" in status or "Stopping" in status:
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FF6347;") # Tomato

    def mark_link_processing(self, processing_link):
        """Marks the currently processing link in the QListWidget with a distinctive color."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            current_link_in_list = item.text().split(". ", 1)[-1]
            if current_link_in_list == processing_link:
                item.setForeground(QColor("#40E0D0"))  # Turquoise for processing, matching accent
                item.setToolTip("Currently downloading...")
            else:
                # Reset color for other links that might have been processing before
                # Only reset if they were not marked as failed (red)
                if item.foreground().color() != QColor("red"):
                    item.setForeground(QColor(Qt.white))
                    item.setToolTip("") # Clear tooltip for non-processing items

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
                item.setForeground(current_color) # Reapply existing color
                item.setFont(current_font)
                current_links_in_widget.append(original_link)
            
            # Re-sync internal download_queue with the list widget's current order
            self.download_queue = current_links_in_widget

        finally:
            self.list_widget.blockSignals(False) # Reconnect signals
            self.link_count_label.setText(f"Total Links: {self.list_widget.count()}")


    def handle_link_completed(self, link_completed):
        """
        Handles a link that has successfully completed download.
        Removes it from the QListWidget, updates input.txt, and re-numbers.
        Provides a desktop notification.
        """
        # Remove from QListWidget
        removed_from_list = False
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            # Extract the actual link from the item text (e.g., "1. http://link.com" -> "http://link.com")
            current_link_in_list = item.text().split(". ", 1)[-1]  
            if current_link_in_list == link_completed:
                self.list_widget.takeItem(i)
                self.log(f"🗑️ Removed completed link '{link_completed[:50]}...' from list.")
                removed_from_list = True
                break
        
        # After removal, re-sync download_queue and update the file
        if removed_from_list:
            self.download_queue = [self.list_widget.item(i).text().split(". ", 1)[-1] for i in range(self.list_widget.count())]
            self._update_input_file()
            self.update_link_numbers() # Re-number the list after removal

        # Desktop Notification
        self.show_notification("Download Completed!", f"Successfully downloaded: {link_completed.split('/')[-1]}")


    def handle_link_failed(self, failed_link, error_message):
        """
        Marks a link in the list widget as failed (red color) and logs the error.
        Does NOT remove it from the list or input.txt, allowing retry.
        """
        found_in_list = False
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            current_link_in_list = item.text().split(". ", 1)[-1]
            if current_link_in_list == failed_link:
                item.setForeground(QColor("red"))
                item.setToolTip(f"Failed: {error_message}")
                self.log(f"❌ Link '{failed_link[:50]}...' failed: {error_message}")
                found_in_list = True
                break
        if not found_in_list:
            self.log(f"❌ Failed link '{failed_link[:50]}...' not found in list (might have been removed). Error: {error_message}")

        self.update_status("⚠️ Error: Check Log")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #FF6347;") # Tomato for error

    def handle_session_finished(self, completed_links, failed_links):
        """
        Called when the DownloaderWorker finishes its entire session.
        Shows a summary and updates UI.
        """
        self.log(f"🏁 Download session finished. {len(completed_links)} completed, {len(failed_links)} failed.")
        self.update_ui_for_idle()

        # Show summary message box
        summary_msg = (
            f"Download Session Completed!\n\n"
            f"✅ Successfully Downloaded: {len(completed_links)} file(s)\n"
            f"❌ Failed Downloads: {len(failed_links)} file(s)"
        )
        if failed_links:
            summary_msg += "\n\nFailed links (check log for details):\n"
            for link in failed_links[:5]: # Show first 5 failed links
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
            # Remove from list widget in reverse order to avoid index issues
            for item in reversed(selected_items):
                row = self.list_widget.row(item)
                link_to_remove = item.text().split(". ", 1)[-1]
                self.list_widget.takeItem(row)
                self.log(f"🗑️ Removed '{link_to_remove[:50]}...' from list.")

            # Update internal download_queue based on current list_widget items
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

    def show_notification(self, title, message):
        """Displays a simple desktop notification."""
        # Using a QSystemTrayIcon for potentially more persistent notifications.
        # This will create a temporary tray icon just for the notification.
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            try:
                # Ensure we have an icon to display in the tray
                icon = self.windowIcon()
                if icon.isNull() and qta:
                    icon = qta.icon('fa5s.download').pixmap(QtCore.QSize(64, 64)) # Get a pixmap from qtawesome icon
                elif icon.isNull():
                    # Fallback to a generic icon if no app icon and no qtawesome
                    icon = QtGui.QIcon(":/qt-project.org/qmessagebox/images/information.png") # A default Qt icon

                tray_icon = QtWidgets.QSystemTrayIcon(icon, self)
                tray_icon.show()
                tray_icon.showMessage(title, message, QtWidgets.QSystemTrayIcon.Information, 5000)
                # To ensure the tray icon is cleaned up, a QTimer can be used,
                # or simply rely on Python's garbage collection if the app is closing soon.
                # For a more robust solution, manage a persistent tray icon object.
                # For this temporary notification, deleting it immediately might make it disappear too fast.
                # A slight delay before deleting is better.
                QtCore.QTimer.singleShot(6000, tray_icon.deleteLater) # Delete after 6 seconds
            except Exception as e:
                self.log(f"Error showing tray notification: {e}. Falling back to QMessageBox.")
                QtWidgets.QMessageBox.information(self, title, message)
        else:
            QtWidgets.QMessageBox.information(self, title, message)


# --------------------- End of GUI Code ---------------------

def main():
    app = QtWidgets.QApplication(sys.argv)
    
    # Register Roboto font if available, otherwise use Segoe UI or default
    font_family = "Roboto"
    if font_family not in QFontDatabase().families():
        font_family = "Segoe UI"
    default_font = QFont(font_family, 10)
    app.setFont(default_font)
    
    window = MainWindow()
    # No need to call window.apply_custom_styles() here.
    # It's already called once in MainWindow.__init__ after apply_stylesheet,
    # and then again whenever the theme is changed via the combo box.
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()