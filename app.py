import cv2
import time
import numpy as np
import os
import insightface
import pandas as pd
from datetime import datetime, timedelta
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Body, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
import json
import shutil
from typing import List, Dict, Optional, Tuple
import asyncio
import uvicorn
from pathlib import Path
from ultralytics import YOLO
import torch
import torchaudio
import soundfile as sf
import sounddevice as sd
import queue
import threading
import librosa
from pydub import AudioSegment
import warnings
from transformers import pipeline
import re
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.units import mm, inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
import traceback
import requests
import base64
import logging
from fastapi.middleware.cors import CORSMiddleware
import scipy.signal as signal
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score
import subprocess
import xlsxwriter
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import uuid
from pydantic import BaseModel, ValidationError
import speechbrain
from speechbrain.inference import SpeakerRecognition
import configparser
from dotenv import load_dotenv
import glob
import tempfile
import contextlib
import psutil
import socket
import sys
from obsws_python import ReqClient
import pythoncom
from pygrabber.dshow_graph import FilterGraph

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SmartMeetingManager")

warnings.filterwarnings("ignore")

# ============================================================================
# OBS RECORDING INTEGRITY MANAGER
# ============================================================================

"""
OBS Recording Integrity Manager
Ensures safe file handling and prevents corruption
"""

class FileIntegrityManager:
    """Manages OBS file operations with integrity guarantees"""
    
    def __init__(self, obs_client=None):
        self.obs_client = obs_client
        self.integrity_lock = threading.RLock()
        self.minimum_finalize_time = 8  # seconds
        self.file_check_retries = 5
        self.file_check_delay = 2  # seconds
        
    def wait_for_recording_stop(self, timeout: int = 30) -> bool:
        """
        Safely wait for OBS to stop recording and finalize file
        Returns True if recording stopped cleanly
        """
        if not self.obs_client:
            logger.warning("No OBS client available, using basic wait")
            time.sleep(self.minimum_finalize_time)
            return True
            
        try:
            # Phase 1: Wait for OBS to acknowledge stop command
            start_time = time.time()
            logger.info("⏳ Waiting for OBS to stop recording...")
            
            for attempt in range(timeout):
                try:
                    status = self.obs_client.get_record_status()
                    if not status.output_active:
                        elapsed = time.time() - start_time
                        logger.info(f"✅ Recording stopped (took {elapsed:.1f}s)")
                        break
                except Exception as e:
                    logger.debug(f"Status check error (attempt {attempt}): {e}")
                
                if time.time() - start_time > timeout:
                    logger.warning(f"⚠️ Stop timeout after {timeout}s")
                    return False
                    
                time.sleep(1)
            
            # Phase 2: Wait for file finalization
            logger.info("⏳ Waiting for OBS to finalize recording file...")
            time.sleep(self.minimum_finalize_time)
            
            # Phase 3: Verify OBS is ready
            try:
                # Check if OBS is responsive
                self.obs_client.get_version()
                logger.info("✅ OBS is responsive and ready")
            except Exception as e:
                logger.warning(f"⚠️ OBS responsiveness check failed: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error in recording stop sequence: {e}")
            # Fallback: wait minimum time
            time.sleep(self.minimum_finalize_time)
            return True
    
    def verify_file_complete(self, file_path: str, min_size_kb: int = 100) -> Tuple[bool, str]:
        """
        Verify a file is complete and not corrupted
        Returns (is_valid, error_message)
        """
        if not os.path.exists(file_path):
            return False, f"File does not exist: {file_path}"
        
        try:
            # Check 1: File size
            size_bytes = os.path.getsize(file_path)
            size_kb = size_bytes / 1024
            
            if size_kb < min_size_kb:
                return False, f"File too small: {size_kb:.1f}KB (minimum: {min_size_kb}KB)"
            
            # Check 2: File extension
            if not file_path.lower().endswith('.mp4'):
                logger.warning(f"⚠️ Non-MP4 file: {file_path}")
            
            # Check 3: Recent modification time (should be stable)
            mtime = os.path.getmtime(file_path)
            time_diff = time.time() - mtime
            
            if time_diff < 3:
                return False, f"File modified {time_diff:.1f}s ago (may still be writing)"
            
            # Check 4: File is readable
            with open(file_path, 'rb') as f:
                header = f.read(100)  # Read first 100 bytes
                if len(header) < 100:
                    return False, "File header incomplete"
                
                # Basic MP4 signature check
                if file_path.lower().endswith('.mp4'):
                    # MP4 files should start with 'ftyp' at position 4
                    if len(header) >= 8 and header[4:8] != b'ftyp':
                        logger.warning("⚠️ File may not be valid MP4 (missing 'ftyp' header)")
            
            logger.info(f"✅ File verified: {os.path.basename(file_path)} ({size_kb:.1f}KB)")
            return True, ""
            
        except PermissionError as e:
            return False, f"Permission error: {e}"
        except OSError as e:
            return False, f"OS error: {e}"
        except Exception as e:
            return False, f"Unexpected error: {e}"
    
    def find_stable_recording_file(self, record_dir: str, start_time: float) -> Optional[str]:
        """
        Find the latest recording file that has stabilized (not being written to)
        """
        with self.integrity_lock:
            video_patterns = ['*.mp4', '*.mkv', '*.mov', '*.flv', '*.avi', '*.ts']
            stable_files = {}
            
            # Multiple passes to ensure stability
            for pass_num in range(3):
                current_files = {}
                
                for pattern in video_patterns:
                    for file_path in glob.glob(os.path.join(record_dir, pattern)):
                        try:
                            mtime = os.path.getmtime(file_path)
                            size = os.path.getsize(file_path)
                            
                            # Only consider files modified after recording start
                            if mtime > start_time - 10:  # 10 second buffer
                                current_files[file_path] = (mtime, size)
                        except (OSError, PermissionError):
                            continue
                
                if pass_num == 0:
                    # First pass, just collect
                    stable_files = current_files.copy()
                else:
                    # Check if file sizes have changed
                    for file_path, (mtime, size) in current_files.items():
                        if file_path in stable_files:
                            # Check if size changed
                            if self._file_changed(file_path, stable_files[file_path][1]):
                                # Remove from stable list if changed
                                stable_files.pop(file_path, None)
                        else:
                            # New file, add to tracking
                            stable_files[file_path] = (mtime, size)
                
                if pass_num < 2:
                    time.sleep(2)  # Wait 2 seconds between checks
            
            # Return most recent stable file
            if stable_files:
                latest_file = max(stable_files.keys(), 
                                key=lambda x: stable_files[x][0])
                return latest_file
            
            return None
    
    def _file_changed(self, file_path: str, previous_size: int) -> bool:
        """Check if file has changed since last check"""
        try:
            current_size = os.path.getsize(file_path)
            return abs(current_size - previous_size) > 1024  # Changed by more than 1KB
        except:
            return True
    
    def safe_move_file(self, source_path: str, target_path: str) -> Tuple[bool, str]:
        """
        Safely move a file with integrity checks and atomic operations
        """
        if not os.path.exists(source_path):
            return False, f"Source file not found: {source_path}"
        
        try:
            # 1. Verify source file
            is_valid, error = self.verify_file_complete(source_path)
            if not is_valid:
                return False, f"Source file invalid: {error}"
            
            # 2. Ensure target directory exists
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)
            
            # 3. Create temp file for atomic move
            temp_target = f"{target_path}.moving"
            
            # 4. Copy with verification
            shutil.copy2(source_path, temp_target)
            
            # 5. Verify copy
            if not os.path.exists(temp_target):
                return False, "Copy failed - temp file not created"
            
            source_size = os.path.getsize(source_path)
            copy_size = os.path.getsize(temp_target)
            
            if source_size != copy_size:
                os.remove(temp_target)
                return False, f"Copy size mismatch: {source_size} != {copy_size}"
            
            # 6. Atomic rename
            if os.path.exists(target_path):
                # Backup existing file
                backup_path = f"{target_path}.backup.{int(time.time())}"
                shutil.move(target_path, backup_path)
                logger.info(f"📦 Backed up existing file to: {backup_path}")
            
            os.rename(temp_target, target_path)
            
            # 7. Verify final file
            is_valid, error = self.verify_file_complete(target_path)
            if not is_valid:
                # Try to restore backup if exists
                if 'backup_path' in locals() and os.path.exists(backup_path):
                    shutil.move(backup_path, target_path)
                return False, f"Final file invalid: {error}"
            
            # 8. Cleanup source if everything succeeded
            try:
                os.remove(source_path)
                logger.info(f"🧹 Cleaned up source file: {os.path.basename(source_path)}")
            except:
                logger.warning(f"⚠️ Could not remove source file: {source_path}")
            
            logger.info(f"✅ File moved successfully: {os.path.basename(target_path)}")
            return True, target_path
            
        except Exception as e:
            logger.error(f"❌ Safe move failed: {e}")
            # Cleanup temp file if exists
            if 'temp_target' in locals() and os.path.exists(temp_target):
                try:
                    os.remove(temp_target)
                except:
                    pass
            return False, str(e)

# ============================================================================
# ENHANCED OBS CONTROLLER WITH INTEGRITY FIXES
# ============================================================================

CONFIG = {
    'obs_websocket_port': 4455,
    'obs_websocket_password': '',
    'virtual_cam_wait_timeout': 20,
    'frame_read_timeout': 5,
    'camera_scan_range': 10,
    'target_camera_name': "OBS Virtual Camera",
    'default_resolution': (1920, 1080),
    'default_fps': 30,
    'recording_save_extension': '.mp4',
    'obs_shutdown_timeout': 15,
}

class RecorderState:
    """State machine for recorder"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    VIRTUAL_CAM_STARTING = "virtual_cam_starting"
    VIRTUAL_CAM_ACTIVE = "virtual_cam_active"
    RECORDING = "recording"
    STOPPING = "stopping"
    ERROR = "error"

class CameraDetector:
    """Advanced camera detection with name-based OBS camera finding"""
    
    @staticmethod
    def _get_devices_safe():
        """Safely get input devices with proper COM initialization"""
        pythoncom.CoInitialize()
        try:
            return FilterGraph().get_input_devices()
        finally:
            pythoncom.CoUninitialize()
    
    @staticmethod
    def get_obs_camera_index():
        """Finds index of camera matching our target name"""
        logger.info(f"🔍 Searching for '{CONFIG['target_camera_name']}'...")
        try:
            devices = CameraDetector._get_devices_safe()
            logger.info("📋 Available cameras:")
            for i, name in enumerate(devices):
                logger.info(f"  Index {i}: {name}")
                if CONFIG['target_camera_name'] in name:
                    logger.info(f"✅ Found matching camera: '{name}' at Index {i}")
                    return i
        except Exception as e:
            logger.error(f"⚠️ Error scanning camera names: {e}")
        
        # Fallback: Try common OBS Virtual Camera indices
        for index in [1, 2, 3, 4]:
            try:
                cap = cv2.VideoCapture(index)
                if cap.isOpened():
                    # Try to get camera name if possible
                    cap.release()
                    logger.info(f"📷 Using fallback camera at Index {index}")
                    return index
            except:
                continue
        
        logger.warning("⚠️ Camera not found by name, defaulting to Index 1")
        return 1
    
    @staticmethod
    def find_obs_virtual_camera(timeout=20):
        """
        Intelligently find OBS Virtual Camera with name-based detection
        """
        logger.info(f"🔍 Searching for '{CONFIG['target_camera_name']}'...")
        logger.info("💡 Make sure OBS Virtual Camera is ON in OBS Studio")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                devices = CameraDetector._get_devices_safe()
                logger.info(f"📋 Available cameras:")
                for i, name in enumerate(devices):
                    logger.info(f"  Index {i}: {name}")
                    if CONFIG['target_camera_name'] in name:
                        logger.info(f"✅ Found matching camera: '{name}' at Index {i}")
                        return i
            except Exception as e:
                logger.error(f"⚠️ Error scanning camera names: {e}")
            
            if time.time() - start_time < timeout - 1:
                time.sleep(1)
        
        logger.error("❌ No suitable camera found")
        return -1

class EnhancedOBSController:
    """Enhanced OBS controller with integrity guarantees"""
    
    def __init__(self, obs_path=None):
        self.client = None
        self.state = RecorderState.DISCONNECTED
        self.obs_process = None
        self.obs_started_by_us = False
        self.obs_path = obs_path or self._find_obs_path()
        self.recording_target = None
        self.last_recording_path = None
        self._event_callbacks = {}
        self.recording_start_time = None
        self.integrity_manager = FileIntegrityManager()
        
        if self.obs_path:
            logger.info(f"✅ Found OBS at: {self.obs_path}")
        else:
            logger.warning("⚠️ OBS Studio not found automatically")
    
    def set_obs_client(self, client):
        """Set the OBS WebSocket client for integrity checks"""
        self.integrity_manager.obs_client = client
        self.client = client
    
    def _find_obs_path(self):
        """Find OBS Studio installation path for version 32"""
        import platform
        system = platform.system()
        
        if system == "Windows":
            common_paths = [
                r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
                r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe",
                os.path.expanduser(r"~\AppData\Local\Programs\obs-studio\bin\64bit\obs64.exe"),
            ]
            
            # Try to find from registry
            try:
                import winreg
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\OBS Studio")
                    install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
                    winreg.CloseKey(key)
                    obs_exe = os.path.join(install_location, "bin", "64bit", "obs64.exe")
                    if os.path.exists(obs_exe):
                        common_paths.insert(0, obs_exe)
                except:
                    pass
                
                # Try 32-bit registry
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\OBS Studio")
                    install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
                    winreg.CloseKey(key)
                    obs_exe = os.path.join(install_location, "bin", "64bit", "obs64.exe")
                    if os.path.exists(obs_exe):
                        common_paths.insert(0, obs_exe)
                except:
                    pass
            except:
                pass
            
        elif system == "Darwin":
            common_paths = [
                "/Applications/OBS.app/Contents/MacOS/OBS",
                "/Applications/OBS Studio.app/Contents/MacOS/OBS",
            ]
        else:
            common_paths = [
                "/usr/bin/obs",
                "/usr/local/bin/obs",
                "/snap/bin/obs-studio",
            ]
        
        for path in common_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def on(self, event, callback):
        """Register event callback"""
        if event not in self._event_callbacks:
            self._event_callbacks[event] = []
        self._event_callbacks[event].append(callback)
    
    def _trigger_event(self, event, *args, **kwargs):
        """Trigger event callbacks"""
        if event in self._event_callbacks:
            for callback in self._event_callbacks[event]:
                try:
                    callback(*args, **kwargs)
                except Exception as e:
                    logger.error(f"⚠️ Event callback error: {e}")
    
    def _check_port_open(self, port):
        """Check if a port is open"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            return result == 0
        except:
            return False
    
    def _is_obs_running(self):
        """Check if OBS is running"""
        try:
            for process in psutil.process_iter(['pid', 'name']):
                try:
                    name = process.info['name']
                    if name and ('obs64' in name.lower() or 'obs-studio' in name.lower()):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.error(f"⚠️ Process check error: {e}")
        return False
    
    def _clean_obs_crash_flag(self):
        """Remove OBS crash flag to prevent safe mode prompt"""
        import platform
        
        if platform.system() != "Windows":
            return
        
        try:
            config_dir = os.path.expandvars(r"%APPDATA%\obs-studio")
            crash_flag_path = os.path.join(config_dir, "crashed")
            
            if os.path.exists(crash_flag_path):
                logger.info("🧹 Removing OBS crash flag...")
                os.remove(crash_flag_path)
                logger.info("✅ Crash flag removed")
            
            # Also check for crash reports
            crash_reports_dir = os.path.join(config_dir, "crashes")
            if os.path.exists(crash_reports_dir):
                try:
                    now = time.time()
                    for file in os.listdir(crash_reports_dir):
                        file_path = os.path.join(crash_reports_dir, file)
                        if os.path.isfile(file_path):
                            if now - os.path.getmtime(file_path) < 86400:
                                os.remove(file_path)
                                logger.info(f"✅ Removed crash report: {file}")
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"⚠️ Crash cleanup failed: {e}")
    
    def _configure_obs_websocket_for_32(self):
        """Configure OBS WebSocket for version 32"""
        import platform
        
        if platform.system() != "Windows":
            return True
        
        try:
            config_path = os.path.expandvars(r"%APPDATA%\obs-studio\global.ini")
            
            if not os.path.exists(config_path):
                logger.info("📝 Creating OBS config...")
                config_dir = os.path.dirname(config_path)
                os.makedirs(config_dir, exist_ok=True)
                
                default_config = """[General]
Language=en

[BasicWindow]
Geometry=@ByteArray(\\x01\\xd9\\xd0\\xcb\\x00\\x03\\x00\\x00\\x00\\x00\\x02\\x9f\\x00\\x00\\x01T\\x00\\x00\\x05\\x14\\x00\\x00\\x02\\xf0\\x00\\x00\\x02\\x9f\\x00\\x00\\x01T\\x00\\x00\\x05\\x14\\x00\\x00\\x02\\xf0\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x07\\x80)

[OBSWebSocket]
ServerEnabled=true
ServerPort=4455
ServerPassword=
DebugEnabled=false
AlertsEnabled=false
AuthRequired=false
"""
                
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(default_config)
                logger.info("✅ Created new OBS config with WebSocket enabled")
                return True
            
            # Read existing config
            with open(config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            websocket_section = False
            needs_update = False
            
            for i, line in enumerate(lines):
                stripped = line.strip()
                
                if stripped == '[OBSWebSocket]':
                    websocket_section = True
                elif websocket_section and stripped.startswith('['):
                    websocket_section = False
                elif websocket_section:
                    if stripped.startswith('ServerEnabled='):
                        if 'false' in stripped.lower():
                            lines[i] = 'ServerEnabled=true\n'
                            needs_update = True
                    elif stripped.startswith('ServerPort='):
                        if '4455' not in stripped:
                            lines[i] = 'ServerPort=4455\n'
                            needs_update = True
                    elif stripped.startswith('AuthRequired='):
                        if 'true' in stripped.lower():
                            lines[i] = 'AuthRequired=false\n'
                            needs_update = True
            
            # Add section if missing
            if not websocket_section:
                logger.info("➕ Adding OBSWebSocket section to config...")
                lines.extend([
                    '\n',
                    '[OBSWebSocket]\n',
                    'ServerEnabled=true\n',
                    'ServerPort=4455\n',
                    'AuthRequired=false\n',
                    'DebugEnabled=false\n',
                    'AlertsEnabled=false\n'
                ])
                needs_update = True
            
            if needs_update:
                logger.info("📝 Updating OBS WebSocket configuration...")
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                logger.info("✅ Configuration updated")
            else:
                logger.info("✅ Configuration already correct")
            
            return True
            
        except Exception as e:
            logger.error(f"⚠️ Config update failed: {e}")
            return False
    
    def _launch_obs_32(self):
        """Launch OBS Studio 32.0.2 with proper settings"""
        if not self.obs_path:
            logger.error("❌ OBS Studio path not found")
            return False
        
        # Clean crash flag first
        self._clean_obs_crash_flag()
        
        # Configure WebSocket
        if not self._configure_obs_websocket_for_32():
            logger.warning("⚠️ Could not configure WebSocket automatically")
        
        logger.info("🚀 Launching OBS Studio 32.0.2...")
        
        try:
            # Check if OBS is already running
            if self._is_obs_running():
                logger.info("ℹ️ OBS already running (will NOT close later)")
                self.obs_started_by_us = False
                # Wait a bit and check WebSocket
                time.sleep(3)
                return self._wait_for_websocket()
            
            # OBS 32.0.2 command-line arguments
            obs_dir = os.path.dirname(self.obs_path)
            
            if sys.platform == "win32":
                cmd = [
                    self.obs_path,
                    "--minimize-to-tray",
                    "--disable-updater"
                ]
                self.obs_process = subprocess.Popen(
                    cmd,
                    cwd=obs_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            elif sys.platform == "darwin":
                cmd = ["open", "-a", "OBS Studio", "--args", "--minimize-to-tray", "--disable-updater"]
                self.obs_process = subprocess.Popen(cmd)
            else:
                cmd = [self.obs_path, "--minimize-to-tray", "--disable-updater"]
                self.obs_process = subprocess.Popen(
                    cmd,
                    cwd=obs_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            
            self.obs_started_by_us = True
            logger.info(f"✅ OBS 32.0.2 launched (PID: {self.obs_process.pid})")
            
            # Give OBS more time to start
            logger.info("⏳ Waiting for OBS to fully start...")
            time.sleep(5)
            
            return self._wait_for_websocket(45)
            
        except Exception as e:
            logger.error(f"❌ Failed to launch OBS: {e}")
            self.obs_started_by_us = False
            return False
    
    def _wait_for_websocket(self, timeout=45):
        """Wait for OBS WebSocket to be ready"""
        logger.info(f"⏳ Waiting for OBS WebSocket (max {timeout}s)...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self._check_port_open(CONFIG['obs_websocket_port']):
                logger.info("✅ OBS WebSocket ready")
                return True
            
            # Check if OBS process is still running
            if self.obs_process and self.obs_process.poll() is not None:
                logger.error("❌ OBS process died")
                return False
            
            time.sleep(1)
        
        logger.error("⚠️ OBS WebSocket timeout")
        return False
    
    def _shutdown_obs_gracefully(self):
        """Shutdown OBS using the shutdown plugin for OBS 32+"""
        try:
            logger.info("🔌 Closing OBS Studio via shutdown plugin...")
            
            if self.client:
                # 1. Stop recording safely
                try:
                    if self.client.get_record_status().output_active:
                        logger.info("🛑 Stopping recording...")
                        self.client.stop_record()
                        time.sleep(2)  # Give time to save file
                except Exception as e:
                    logger.error(f"⚠️ Could not stop recording: {e}")
                
                # 2. Stop Virtual Camera
                try:
                    if self.client.get_virtual_cam_status().output_active:
                        logger.info("📷 Stopping virtual camera...")
                        self.client.stop_virtual_cam()
                        time.sleep(1)
                except Exception as e:
                    logger.error(f"⚠️ Could not stop virtual camera: {e}")
                
                # 3. Save OBS settings
                try:
                    self.client.save()
                    time.sleep(0.5)
                    logger.info("💾 Settings saved")
                except Exception as e:
                    logger.error(f"⚠️ Could not save settings: {e}")
                
                # 4. CRITICAL: Use the shutdown plugin via vendor request
                try:
                    logger.info("🔄 Sending shutdown command via plugin...")
                    
                    # Method 1: Try the vendor request (recommended)
                    try:
                        response = self.client.req.call_vendor_request({
                            "vendorName": "obs-shutdown-plugin",
                            "requestType": "shutdown",
                            "requestData": {}
                        })
                        logger.info(f"✅ Shutdown plugin command sent: {response}")
                    except AttributeError:
                        # Method 2: Fallback to direct request
                        response = self.client.send("CallVendorRequest", {
                            "vendorName": "obs-shutdown-plugin",
                            "requestType": "shutdown",
                            "requestData": {}
                        })
                        logger.info(f"✅ Shutdown command sent (fallback): {response}")
                    
                    # Give OBS time to close
                    logger.info("⏳ Waiting for OBS to close gracefully...")
                    time.sleep(5)
                    
                except Exception as e:
                    logger.error(f"⚠️ Plugin shutdown failed: {e}")
                    logger.info("💡 The plugin might not be installed correctly")
                    logger.info("   Using fallback termination...")
            
            # 5. Fallback if plugin didn't work or OBS still running
            if self.obs_started_by_us and self.obs_process:
                if self._is_obs_running():
                    logger.info("🛑 OBS still running, terminating process...")
                    try:
                        self.obs_process.terminate()
                        self.obs_process.wait(timeout=10)
                        logger.info("✅ OBS terminated")
                    except:
                        try:
                            self.obs_process.kill()
                            self.obs_process.wait(timeout=5)
                            logger.info("✅ OBS killed")
                        except Exception as e:
                            logger.error(f"❌ Could not terminate OBS: {e}")
            
            # 6. Clean crash flags regardless of shutdown method
            logger.info("🧹 Cleaning OBS crash flags...")
            self._clean_obs_crash_flag()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Shutdown error: {e}")
            # Still clean crash flags even if shutdown failed
            self._clean_obs_crash_flag()
            return False
    
    def test_shutdown_plugin(self):
        """Test if the shutdown plugin is installed and working"""
        if not self.client:
            logger.error("❌ Not connected to OBS")
            return False
        
        try:
            logger.info("🔍 Testing shutdown plugin...")
            
            # Try to call the plugin
            response = self.client.req.call_vendor_request({
                "vendorName": "obs-shutdown-plugin",
                "requestType": "get_status",  # Test command, doesn't shutdown
                "requestData": {}
            })
            
            logger.info(f"✅ Shutdown plugin detected: {response}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Shutdown plugin not available: {e}")
            logger.info("\n💡 TROUBLESHOOTING:")
            logger.info("   1. Make sure you copied BOTH files to:")
            logger.info("      C:\\Program Files\\obs-studio\\obs-plugins\\64bit\\")
            logger.info("   2. Restart OBS Studio completely")
            logger.info("   3. Check OBS → Tools → WebSocket Server Settings")
            logger.info("      You should see 'Shutdown Plugin' section")
            return False
    
    def connect(self):
        """Connect to OBS Studio"""
        if self.state != RecorderState.DISCONNECTED:
            logger.warning(f"⚠️ Already in state: {self.state}")
            return True
        
        self.state = RecorderState.CONNECTING
        self._trigger_event('state_changed', self.state)
        
        max_attempts = 2
        
        for attempt in range(max_attempts):
            try:
                logger.info(f"\n🔄 Connection attempt {attempt + 1}/{max_attempts}")
                
                # Check if OBS is running
                if not self._is_obs_running():
                    logger.info("OBS not running, launching...")
                    if not self._launch_obs_32():
                        if attempt == max_attempts - 1:
                            logger.error("❌ Failed to launch OBS")
                            self.state = RecorderState.ERROR
                            self._trigger_event('state_changed', self.state)
                            return False
                        continue
                
                # Connect via WebSocket
                logger.info(f"🔗 Connecting to WebSocket (localhost:{CONFIG['obs_websocket_port']})...")
                self.client = ReqClient(
                    host='localhost',
                    port=CONFIG['obs_websocket_port'],
                    password=CONFIG['obs_websocket_password'],
                    timeout=15
                )
                
                # Test connection
                version = self.client.get_version()
                logger.info(f"✅ Connected to OBS v{version.obs_version}")
                
                # Set client for integrity manager
                self.set_obs_client(self.client)
                
                self.state = RecorderState.CONNECTED
                self._trigger_event('state_changed', self.state)
                self._trigger_event('connected', version)
                
                return True
                
            except Exception as e:
                logger.error(f"❌ Connection failed: {e}")
                
                if attempt < max_attempts - 1:
                    logger.info(f"Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    logger.error("❌ All connection attempts failed")
                    logger.info("\n💡 TROUBLESHOOTING for OBS 32.0.2:")
                    logger.info("   1. Open OBS Studio manually")
                    logger.info("   2. Go to Tools → WebSocket Server Settings")
                    logger.info("   3. Enable WebSocket server")
                    logger.info("   4. Set Port: 4455")
                    logger.info("   5. Uncheck 'Enable Authentication'")
                    logger.info("   6. Click OK and restart OBS")
                    logger.info("   7. Try connecting again")
                    self.state = RecorderState.ERROR
                    self._trigger_event('state_changed', self.state)
        
        return False

    def ensure_virtual_camera_active(self):
        """Ensure OBS Virtual Camera is active for live feed"""
        if not self.client:
            return False
        
        try:
            vcam_status = self.client.get_virtual_cam_status()
            if not vcam_status.output_active:
                logger.info("🎥 Starting OBS Virtual Camera for live feed...")
                self.client.start_virtual_cam()
                
                # Wait for virtual camera to become active
                start_time = time.time()
                while time.time() - start_time < 10:
                    vcam_status = self.client.get_virtual_cam_status()
                    if vcam_status.output_active:
                        logger.info("✅ OBS Virtual Camera ACTIVE")
                        return True
                    time.sleep(1)
                
                logger.warning("⚠️ Virtual camera might not be fully active")
                return False
            
            return True
        except Exception as e:
            logger.error(f"❌ Error ensuring virtual camera: {e}")
            return False
    
    def start_virtual_camera(self):
        """Start OBS Virtual Camera - OBS 32 version"""
        if self.state not in [RecorderState.CONNECTED, RecorderState.VIRTUAL_CAM_ACTIVE]:
            logger.error(f"❌ Cannot start virtual camera in state: {self.state}")
            return False
        
        logger.info("🎥 Starting OBS Virtual Camera...")
        
        try:
            self.client.start_virtual_cam()
        except Exception as e:
            logger.warning(f"⚠️ Virtual cam start error (might already be running): {e}")
        
        # Wait longer for OBS 32
        start = time.time()
        logger.info("⏳ Waiting for virtual camera...")
        
        while time.time() - start < CONFIG['virtual_cam_wait_timeout']:
            try:
                status = self.client.get_virtual_cam_status()
                if status.output_active:
                    logger.info("✅ Virtual Camera ACTIVE")
                    self.state = RecorderState.VIRTUAL_CAM_ACTIVE
                    self._trigger_event('state_changed', self.state)
                    self._trigger_event('virtual_cam_started')
                    return True
            except Exception as e:
                logger.warning(f"⚠️ Status check failed: {e}")
            
            time.sleep(1)
        
        logger.warning("⚠️ Virtual camera might not be fully active")
        logger.info("💡 Please check OBS and ensure Virtual Camera is enabled")
        self.state = RecorderState.VIRTUAL_CAM_ACTIVE  # Assume active
        self._trigger_event('state_changed', self.state)
        return True
    
    def setup_recording_camera(self, camera_index=0):
        """NO-OP: OBS should already be configured with camera and audio"""
        logger.info("✅ Using existing OBS scene configuration")
        logger.info("💡 OBS should already have camera and audio sources configured")
        return True
    
    def _check_obs_recording_ready(self) -> bool:
        """Check if OBS is ready to record"""
        try:
            # Check recording status
            record_status = self.client.get_record_status()
            
            if record_status.output_active:
                logger.warning("⚠️ OBS is already recording")
                return False
            
            # Check OBS version
            version = self.client.get_version()
            logger.info(f"📋 OBS Version: {version.obs_version}")
            
            # Check available disk space in recording directory
            try:
                record_dir = self.client.get_record_directory().record_directory
                free_space = shutil.disk_usage(record_dir).free / (1024**3)  # GB
                
                if free_space < 1.0:  # Less than 1GB free
                    logger.warning(f"⚠️ Low disk space: {free_space:.1f}GB free")
                    return False
                
                logger.info(f"💾 Disk space: {free_space:.1f}GB free in {record_dir}")
            except:
                pass  # Disk check optional
            
            return True
            
        except Exception as e:
            logger.error(f"❌ OBS readiness check failed: {e}")
            return False
    
    def start_recording(self, folder_path=None, filename=None):
        """Start recording with pre-flight checks"""
        if self.state not in [RecorderState.CONNECTED, RecorderState.VIRTUAL_CAM_ACTIVE]:
            logger.error(f"❌ Cannot start recording in state: {self.state}")
            return False, "OBS not connected"
        
        try:
            # Pre-flight checks
            if not self._check_obs_recording_ready():
                return False, "OBS not ready for recording"
            
            if folder_path and filename:
                self.set_recording_target(folder_path, filename)
            
            logger.info("🎬 Starting recording with integrity checks...")
            
            # FIX 1: Ensure virtual camera is started BEFORE recording
            if self.state != RecorderState.VIRTUAL_CAM_ACTIVE:
                logger.info("📷 Starting OBS Virtual Camera for live feed...")
                if not self.start_virtual_camera():
                    logger.warning("⚠️ Virtual camera might not be fully active, but proceeding with recording")
            
            # Clear any previous recording path
            self.last_recording_path = None
            
            # Start recording
            self.recording_start_time = time.time()
            self.client.start_record()
            
            # Verify recording actually started
            time.sleep(2)
            status = self.client.get_record_status()
            
            if status.output_active:
                self.state = RecorderState.RECORDING
                self._trigger_event('state_changed', self.state)
                self._trigger_event('recording_started', self.recording_start_time)
                
                logger.info("✅ Recording STARTED with integrity monitoring")
                logger.info(f"📊 OBS Recording Status: Active")
                logger.info(f"📸 Virtual Camera: {'Active' if self.state == RecorderState.VIRTUAL_CAM_ACTIVE else 'Starting'}")
                
                if self.recording_target:
                    logger.info(f"📁 Target: {self.recording_target['full_path']}")
                
                return True, "Recording started"
            else:
                logger.error("❌ Recording did not start (OBS returned inactive)")
                self.state = RecorderState.CONNECTED
                return False, "Recording did not start"
                
        except Exception as e:
            logger.error(f"❌ Failed to start recording: {e}")
            self.state = RecorderState.ERROR
            self._trigger_event('state_changed', self.state)
            return False, str(e)
    
    def stop_recording(self) -> Tuple[bool, str]:
        """
        Stop recording safely with integrity guarantees
        Returns (success, message_or_path)
        """
        if self.state != RecorderState.RECORDING:
            logger.warning(f"⚠️ Not recording (state: {self.state})")
            return False, "Not recording"
        
        self.state = RecorderState.STOPPING
        self._trigger_event('state_changed', self.state)
        
        try:
            logger.info("🛑 Stopping recording with safe shutdown...")
            
            # 1. Stop recording
            self.client.stop_record()
            
            # 2. Wait for OBS to finalize file (CRITICAL FIX)
            success = self.integrity_manager.wait_for_recording_stop(timeout=30)
            
            if not success:
                logger.error("❌ Recording stop verification failed")
                self.state = RecorderState.ERROR
                return False, "Recording stop verification failed"
            
            # 3. Stop virtual camera (optional, but do it after file is safe)
            try:
                vcam_status = self.client.get_virtual_cam_status()
                if vcam_status.output_active:
                    logger.info("📷 Stopping virtual camera...")
                    self.client.stop_virtual_cam()
                    time.sleep(1)
            except Exception as e:
                logger.debug(f"Virtual camera stop warning: {e}")
            
            # 4. Find the recording file
            recording_file = self._find_latest_recording_safe()
            
            if not recording_file:
                logger.error("❌ Could not find recording file")
                self.state = RecorderState.CONNECTED
                return False, "No recording file found"
            
            # 5. Verify file integrity
            is_valid, error = self.integrity_manager.verify_file_complete(recording_file)
            
            if not is_valid:
                logger.error(f"❌ Recording file corrupted: {error}")
                # Don't move corrupted files
                self.last_recording_path = recording_file
                self.state = RecorderState.CONNECTED
                return False, f"Recording corrupted: {error}"
            
            # 6. Move to target location (if specified)
            final_path = recording_file
            if self.recording_target:
                move_success, move_result = self._move_to_target_safe(recording_file)
                
                if move_success:
                    final_path = move_result
                else:
                    logger.error(f"❌ Failed to move file: {move_result}")
                    final_path = recording_file
            
            self.last_recording_path = final_path
            
            # 7. Final verification
            if os.path.exists(final_path):
                file_size_mb = os.path.getsize(final_path) / (1024 * 1024)
                logger.info(f"✅ Recording saved: {os.path.basename(final_path)} ({file_size_mb:.1f} MB)")
                
                # Double-check integrity
                is_valid, error = self.integrity_manager.verify_file_complete(final_path)
                if not is_valid:
                    logger.error(f"❌ FINAL VERIFICATION FAILED: {error}")
                    self.state = RecorderState.ERROR
                    return False, f"File corrupted after move: {error}"
            else:
                logger.error("❌ Final file does not exist")
                self.state = RecorderState.ERROR
                return False, "Final file missing"
            
            # Success
            self.state = RecorderState.CONNECTED
            self._trigger_event('state_changed', self.state)
            self._trigger_event('recording_stopped', final_path)
            self.recording_target = None
            
            return True, final_path
            
        except Exception as e:
            logger.error(f"❌ Error stopping recording: {e}")
            self.state = RecorderState.ERROR
            self._trigger_event('state_changed', self.state)
            return False, str(e)
    
    def _find_latest_recording_safe(self) -> Optional[str]:
        """
        Find the latest recording file with multiple safety checks
        """
        try:
            response = self.client.get_record_directory()
            record_dir = response.record_directory
            
            if not os.path.exists(record_dir):
                logger.error(f"❌ Recording directory not found: {record_dir}")
                return None
            
            # Use integrity manager to find stable file
            file_path = self.integrity_manager.find_stable_recording_file(
                record_dir, 
                self.recording_start_time
            )
            
            if file_path:
                logger.info(f"📄 Found recording: {os.path.basename(file_path)}")
                return file_path
            else:
                # Fallback: look for any recent video file
                video_patterns = ['*.mp4', '*.mkv', '*.mov', '*.flv', '*.avi']
                latest_file = None
                latest_mtime = 0
                
                for pattern in video_patterns:
                    for file_path in glob.glob(os.path.join(record_dir, pattern)):
                        try:
                            mtime = os.path.getmtime(file_path)
                            # Only consider files created after recording start
                            if mtime > self.recording_start_time - 5:
                                if mtime > latest_mtime:
                                    latest_mtime = mtime
                                    latest_file = file_path
                        except:
                            continue
                
                if latest_file:
                    logger.warning(f"⚠️ Using fallback file detection: {os.path.basename(latest_file)}")
                    return latest_file
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Failed to find recording: {e}")
            return None
    
    def _move_to_target_safe(self, source_path: str) -> Tuple[bool, str]:
        """
        Safely move recording to target location
        """
        if not self.recording_target:
            return False, "No recording target set"
        
        target_path = self.recording_target['full_path']
        
        # Handle filename conflicts
        if os.path.exists(target_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base, ext = os.path.splitext(self.recording_target['filename'])
            new_filename = f"{base}_{timestamp}{ext}"
            target_path = os.path.join(self.recording_target['folder'], new_filename)
            logger.info(f"📝 Target file exists, using: {new_filename}")
        
        # Use integrity manager for safe move
        success, result = self.integrity_manager.safe_move_file(source_path, target_path)
        
        if success:
            return True, result
        else:
            # If safe move failed, keep original file
            logger.error(f"❌ Safe move failed, keeping original: {result}")
            return False, source_path
    
    def set_recording_target(self, folder_path, filename):
        """Set where to save the recording"""
        try:
            os.makedirs(folder_path, exist_ok=True)
            
            if not filename.lower().endswith('.mp4'):
                filename += CONFIG['recording_save_extension']
            
            import re
            filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
            
            self.recording_target = {
                'folder': folder_path,
                'filename': filename,
                'full_path': os.path.join(folder_path, filename)
            }
            
            logger.info(f"✅ Recording target set: {self.recording_target['full_path']}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to set recording target: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from OBS with proper shutdown for OBS 32"""
        try:
            if self.state == RecorderState.RECORDING:
                self.stop_recording()
            
            if self.client:
                try:
                    # Save settings before disconnecting
                    self.client.save()
                    logger.info("💾 OBS settings saved")
                except:
                    pass
            
            # Use the new graceful shutdown method if we started OBS
            if self.obs_started_by_us and self.obs_process:
                logger.info("🔌 Closing OBS Studio (started by app)...")
                if not self._shutdown_obs_gracefully():
                    logger.warning("⚠️ Graceful shutdown failed, trying fallback...")
                    try:
                        # Fallback: still better than terminate/kill
                        if self.client:
                            try:
                                self.client.send("Quit")
                                time.sleep(5)
                            except:
                                pass
                        
                        # Last resort, but should rarely be needed
                        if self._is_obs_running():
                            logger.warning("⚠️ OBS still running, forcing termination...")
                            self.obs_process.terminate()
                            self.obs_process.wait(timeout=10)
                    except Exception as e:
                        logger.error(f"⚠️ Error in fallback shutdown: {e}")
            
            elif self._is_obs_running():
                logger.info("ℹ️ OBS still running (was started externally)")
            
            if self.client:
                self.client = None
            
            self.state = RecorderState.DISCONNECTED
            self._trigger_event('state_changed', self.state)
            logger.info("✅ Disconnected")
            
        except Exception as e:
            logger.error(f"⚠️ Error during disconnect: {e}")
            self.state = RecorderState.DISCONNECTED
            self._trigger_event('state_changed', self.state)
    
    def get_status(self):
        """Get current status"""
        if not self.client:
            return f"State: {self.state}"
        
        try:
            record_status = self.client.get_record_status()
            virtual_cam_status = self.client.get_virtual_cam_status()
            
            status_text = f"State: {self.state}\n"
            status_text += f"Recording: {'ACTIVE' if record_status.output_active else 'INACTIVE'}\n"
            status_text += f"Virtual Camera: {'ACTIVE' if virtual_cam_status.output_active else 'INACTIVE'}"
            
            if self.state == RecorderState.RECORDING:
                elapsed = time.time() - self.recording_start_time
                status_text += f"\nRecording time: {int(elapsed)}s"
            
            return status_text
            
        except Exception as e:
            return f"State: {self.state}\nError: {str(e)}"

# ============================================================================
# CONFIGURATION MANAGEMENT CLASS
# ============================================================================
class Config:
    def __init__(self):
        self.SIMILARITY_THRESHOLD = 0.4
        self.BASE_DIR = Path(__file__).parent
        self.KNOWN_FACES_DIR = self.BASE_DIR / "KnownFaces"
        self.AUDIO_SAMPLES_DIR = self.BASE_DIR / "voice_samples"
        self.MEETINGS_DATA_DIR = self.BASE_DIR / "MeetingsData"
        self.STATIC_DIR = self.BASE_DIR / "static"
        
        # Model configurations
        self.FACE_MODEL_CONFIG = {
            'allowed_modules': ['detection', 'recognition'],
            'providers': ['CUDAExecutionProvider', 'CPUExecutionProvider'] 
            if torch.cuda.is_available() else ['CPUExecutionProvider']
        }
        
        # Load from environment
        self.load_env()
    
    def load_env(self):
        # LLM Configuration - NO FALLBACKS, only from .env
        self.LLM_API_KEY = os.getenv("LLM_API_KEY")
        if not self.LLM_API_KEY:
            raise ValueError("LLM_API_KEY environment variable is required")
        
        self.LLM_MODEL = os.getenv("LLM_MODEL")
        if not self.LLM_MODEL:
            raise ValueError("LLM_MODEL environment variable is required")
        
        self.LLM_BASE_URL = os.getenv("LLM_BASE_URL")
        if not self.LLM_BASE_URL:
            raise ValueError("LLM_BASE_URL environment variable is required")
        
        self.LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
        self.LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4000"))
        self.LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

        # Email Configuration - NO FALLBACKS for required fields  
        self.EMAIL_SENDER = os.getenv("EMAIL_SENDER")
        if not self.EMAIL_SENDER:
            raise ValueError("EMAIL_SENDER environment variable is required")
        
        self.EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
        if not self.EMAIL_PASSWORD:
            raise ValueError("EMAIL_PASSWORD environment variable is required")
        
        self.EMAIL_SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
        self.EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        self.EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
        self.EMAIL_DEFAULT_SUBJECT = os.getenv("EMAIL_DEFAULT_SUBJECT", "Meeting Summary - {meeting_id}")
        self.EMAIL_DEFAULT_BODY = os.getenv("EMAIL_DEFAULT_BODY", "Please find the meeting files attached.\n\nMeeting ID: {meeting_id}\nDate: {date}\n\nBest regards,\nMeetingSense System")

        # Application Settings
        self.PORT = int(os.getenv("PORT", "8000"))
        self.HOST = os.getenv("HOST", "0.0.0.0")
        self.DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Create global config instance
config = Config()

# ============================================================================
# GLOBAL VARIABLES (from config)
# ============================================================================
SIMILARITY_THRESHOLD = config.SIMILARITY_THRESHOLD
BASE_DIR = str(config.BASE_DIR)
KNOWN_FACES_DIR = str(config.KNOWN_FACES_DIR)
AUDIO_SAMPLES_DIR = str(config.AUDIO_SAMPLES_DIR)
MEETINGS_DATA_DIR = str(config.MEETINGS_DATA_DIR)
STATIC_DIR = str(config.STATIC_DIR)

os.makedirs(KNOWN_FACES_DIR, exist_ok=True)
os.makedirs(AUDIO_SAMPLES_DIR, exist_ok=True)
os.makedirs(MEETINGS_DATA_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app = FastAPI(title="Smart Meeting Manager - Audio and Attendance System", version="13.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

attendance_active = False
recording_active = False

active_connections: List[WebSocket] = []
meeting_status = {
    'attendance_active': False,
    'audio_recording_active': False,
    'unified_recording_active': False,
    'video_recording_active': False,
}

# Initialize Enhanced OBS Controller globally
obs_controller = EnhancedOBSController()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

global_frame = None
frame_lock = threading.Lock()

class MeetingCreate(BaseModel):
    title: str
    agenda: str = ""

def clean_meeting_title_for_display(original_title):
    if not original_title:
        return "Meeting Discussion"
    
    title = re.sub(r'meeting_\d{8}_\d{6}_[a-f0-9]+', '', original_title)
    title = re.sub(r'\d{8}_\d{6}', '', title)
    title = re.sub(r'^meeting_', '', title)
    title = title.replace('_', ' ').strip()
    
    if not title:
        title = original_title.replace('_', ' ').replace('-', ' ').strip()
    
    title = ' '.join(word.capitalize() for word in title.split())
    
    return title if title else "Meeting Discussion"

def validate_and_convert_audio(audio_content, filename, target_sample_rate=16000):
    try:
        temp_path = f"temp_{uuid.uuid4().hex}{Path(filename).suffix}"
        with open(temp_path, "wb") as f:
            f.write(audio_content)
        
        try:
            audio_data, sr = sf.read(temp_path)
        except:
            try:
                audio_data, sr = librosa.load(temp_path, sr=target_sample_rate)
            except:
                try:
                    waveform, sr = torchaudio.load(temp_path)
                    audio_data = waveform.numpy().flatten()
                except Exception as e:
                    raise Exception(f"Failed to load audio file: {e}")
        
        if audio_data is None or len(audio_data) == 0:
            raise Exception("Empty audio data")
        
        duration = len(audio_data) / sr
        if duration < 1.0:
            raise Exception(f"Audio too short ({duration:.1f}s). Minimum 1 second required.")
        if duration > 30.0:
            raise Exception(f"Audio too long ({duration:.1f}s). Maximum 30 seconds allowed.")
        
        max_val = np.max(np.abs(audio_data))
        if max_val > 0:
            audio_data = audio_data / max_val * 0.8
        
        if sr != target_sample_rate:
            audio_data = librosa.resample(audio_data, orig_sr=sr, target_sr=target_sample_rate)
            sr = target_sample_rate
        
        nyquist = sr / 2
        high_pass_freq = 80
        if high_pass_freq < nyquist:
            sos = signal.butter(4, high_pass_freq/nyquist, 'highpass', output='sos')
            audio_data = signal.sosfilt(sos, audio_data)
        
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=0)
        
        audio_data_int16 = (audio_data * 32767).astype(np.int16)
        
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return True, audio_data_int16, sr, None
        
    except (FileNotFoundError, PermissionError) as e:
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False, None, None, f"File error: {str(e)}"
    except (IOError, OSError) as e:
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False, None, None, f"IO error: {str(e)}"
    except Exception as e:
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False, None, None, f"Unexpected error: {str(e)}"

class EnhancedAudioDiarizer:
    def __init__(self):
        self.speaker_model = None
        self.whisper_model = None
        self.voice_embeddings = {}
        self.models_initialized = False
        self.current_progress = 0
        self.progress_message = ""
        
    def _initialize_models(self):
        if self.models_initialized:
            return
            
        try:
            logger.info("Loading diarization models...")
            
            self.speaker_model = SpeakerRecognition.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir="pretrained_ecapa"
            )
            
            try:
                if torch.cuda.is_available():
                    device = 0
                    torch_dtype = torch.float16
                else:
                    device = -1
                    torch_dtype = torch.float32
                
                self.whisper_model = pipeline(
                    "automatic-speech-recognition",
                    model="openai/whisper-large-v3",
                    device=device,
                    torch_dtype=torch_dtype,
                    chunk_length_s=30,
                    batch_size=4,
                )
                logger.info("✅ Whisper large-v3 model loaded")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load large-v3: {e}")
                try:
                    self.whisper_model = pipeline(
                        "automatic-speech-recognition",
                        model="openai/whisper-large-v3",
                        device=device,
                        torch_dtype=torch_dtype,
                    )
                    logger.info("✅ Whisper medium model loaded (fallback)")
                except Exception as e2:
                    logger.error(f"❌ Failed to load any Whisper model: {e2}")
                    self.whisper_model = None
            
            self.models_initialized = True
            logger.info("✅ Diarization models loaded successfully")
        except Exception as e:
            logger.error(f"❌ Failed to load diarization models: {e}")
    
    def preprocess_audio_for_speaker_id(self, waveform, sr, target_sr=16000):
        if waveform.dim() > 1 and waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        
        if sr != target_sr:
            waveform = torchaudio.functional.resample(waveform, sr, target_sr)
            sr = target_sr
        
        audio_np = waveform.squeeze().numpy()
        
        sos = signal.butter(4, 80, 'hp', fs=sr, output='sos')
        audio_np = signal.sosfilt(sos, audio_np)
        
        rms = np.sqrt(np.mean(audio_np**2))
        if rms > 0:
            audio_np = audio_np / rms * 0.1
        
        audio_np = np.clip(audio_np, -1.0, 1.0)
        
        return torch.tensor(audio_np).unsqueeze(0), sr
    
    def preprocess_audio_simple(self, audio_np, sr):
        nyquist = sr / 2
        low_cutoff = 80 / nyquist
        high_cutoff = min(8000 / nyquist, 0.95)
        
        try:
            sos = signal.butter(4, [low_cutoff, high_cutoff], btype='band', output='sos')
            audio_np = signal.sosfilt(sos, audio_np)
        except Exception as e:
            logger.warning(f"    ⚠️ Band-pass filter failed: {e}")
        
        rms = np.sqrt(np.mean(audio_np**2))
        if rms > 1e-6:
            target_rms = 0.05
            audio_np = audio_np * (target_rms / rms)
        
        audio_np = np.clip(audio_np, -0.99, 0.99)
        
        return audio_np
    
    @staticmethod
    def remove_repeated_words(text):
        if not text or len(text) < 10:
            return text
            
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) < 2:
            return text
            
        deduplicated = []
        last_sentence = ""
        
        for sentence in sentences:
            words1 = set(last_sentence.lower().split())
            words2 = set(sentence.lower().split())
            
            if last_sentence:
                common_words = words1.intersection(words2)
                if len(words1) > 0:
                    similarity = len(common_words) / len(words1)
                    if similarity > 0.7:
                        continue
            
            sentence_words = sentence.split()
            if len(sentence_words) > 10:
                for i in range(3, len(sentence_words) // 2 + 1):
                    for j in range(len(sentence_words) - i * 2 + 1):
                        segment1 = ' '.join(sentence_words[j:j+i])
                        segment2 = ' '.join(sentence_words[j+i:j+i*2])
                        if segment1 == segment2:
                            sentence = ' '.join(sentence_words[:j+i])
                            break
            
            deduplicated.append(sentence)
            last_sentence = sentence
        
        result = '. '.join(deduplicated)
        if text.endswith('.') and not result.endswith('.'):
            result += '.'
            
        return result
    
    def convert_to_wav(self, audio_path):
        if not audio_path.endswith(".wav"):
            logger.info("Converting input to WAV …")
            sound = AudioSegment.from_file(audio_path)
            wav_path = os.path.splitext(audio_path)[0] + ".wav"
            sound = sound.set_channels(1).set_frame_rate(16000)
            sound.export(wav_path, format="wav")
            return wav_path
        return audio_path
    
    def load_embedding_model(self):
        if self.speaker_model is not None:
            return self.speaker_model
            
        logger.info("Loading embedding model …")
        self.speaker_model = SpeakerRecognition.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir="pretrained_ecapa"
        )
        return self.speaker_model
    
    def load_voice_samples(self, model, samples_dir="voice_samples"):
        logger.info(f"\n📁 Loading voice samples from '{samples_dir}/' …")
        
        if not os.path.exists(samples_dir):
            logger.warning(f"⚠️ Warning: '{samples_dir}/' directory not found. No voice samples loaded.")
            return {}
        
        voice_embeddings = {}
        
        for filename in os.listdir(samples_dir):
            if filename.lower().endswith(('.mp3', '.wav', '.m4a', '.flac')):
                speaker_name = os.path.splitext(filename)[0]
                sample_path = os.path.join(samples_dir, filename)
                
                wav_path = self.convert_to_wav(sample_path)
                
                try:
                    waveform, sr = torchaudio.load(wav_path)
                    
                    waveform, sr = self.preprocess_audio_for_speaker_id(waveform, sr)
                    
                    chunk_duration = 3.0
                    chunk_samples = int(chunk_duration * sr)
                    total_samples = waveform.shape[1]
                    
                    embeddings_list = []
                    
                    for start_idx in range(0, total_samples - chunk_samples, chunk_samples // 2):
                        end_idx = start_idx + chunk_samples
                        chunk = waveform[:, start_idx:end_idx]
                        
                        with torch.no_grad():
                            emb = model.encode_batch(chunk).squeeze().cpu().numpy()
                            embeddings_list.append(emb)
                    
                    if embeddings_list:
                        avg_embedding = np.mean(embeddings_list, axis=0)
                        voice_embeddings[speaker_name] = avg_embedding
                        logger.info(f"  ✅ Loaded: {speaker_name} ({len(embeddings_list)} chunks averaged)")
                    else:
                        logger.info(f"  ⚠️ {speaker_name}: audio too short")
                        
                except Exception as e:
                    logger.error(f"  ❌ Failed to load {filename}: {e}")
        
        logger.info(f"\n📊 Total voice samples loaded: {len(voice_embeddings)}")
        return voice_embeddings
    
    def identify_speaker(self, segment_embedding, voice_embeddings, model, min_threshold=0.0):
        if not voice_embeddings:
            return None, 0.0
        
        scores = {}
        
        for speaker_name, sample_embedding in voice_embeddings.items():
            seg_norm = segment_embedding / (np.linalg.norm(segment_embedding) + 1e-8)
            sample_norm = sample_embedding / (np.linalg.norm(sample_embedding) + 1e-8)
            
            score = np.dot(seg_norm, sample_norm)
            scores[speaker_name] = score
        
        best_match = max(scores, key=scores.get)
        best_score = scores[best_match]
        
        logger.info(f"    🔍 Similarity scores:")
        for name, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            marker = "👉" if name == best_match else "  "
            logger.info(f"    {marker} {name}: {score:.4f}")
        
        return best_match, best_score
    
    def segment_audio_for_speaker_id(self, wav_path, segment_duration=2.0, overlap=0.5):
        logger.info("Segmenting audio for speaker identification…")
        waveform, sr = torchaudio.load(wav_path)
        
        waveform, sr = self.preprocess_audio_for_speaker_id(waveform, sr)

        segment_len = int(segment_duration * sr)
        hop_len = int((segment_duration - overlap) * sr)
        total_len = waveform.shape[1]

        segments, timestamps = [], []
        for start in range(0, total_len - segment_len, hop_len):
            end = start + segment_len
            segments.append(waveform[:, start:end])
            timestamps.append((start / sr, end / sr))

        logger.info(f"Total segments created for speaker ID: {len(segments)}")
        return segments, timestamps, sr
    
    def extract_embeddings(self, model, segments):
        logger.info("Computing embeddings …")
        embeddings = []
        for segment in segments:
            with torch.no_grad():
                emb = model.encode_batch(segment).squeeze().cpu().numpy()
                embeddings.append(emb)
        embeddings = np.array(embeddings)
        logger.info(f"Computed embeddings for {len(embeddings)} segments")
        return embeddings
    
    def estimate_speakers(self, embeddings, max_speakers=8):
        logger.info("Estimating number of speakers …")
        best_k = 3
        best_score = -1

        for k in range(2, min(max_speakers, len(embeddings))):
            clustering = AgglomerativeClustering(n_clusters=k)
            labels = clustering.fit_predict(embeddings)
            if len(set(labels)) == 1:
                continue
            score = silhouette_score(embeddings, labels)
            if score > best_score:
                best_score = score
                best_k = k

        logger.info(f"Estimated number of speakers: {best_k}")
        return best_k
    
    def diarize(self, embeddings, timestamps):
        num_speakers = self.estimate_speakers(embeddings)
        clustering = AgglomerativeClustering(n_clusters=num_speakers)
        labels = clustering.fit_predict(embeddings)

        diarization = []
        for i, (start, end) in enumerate(timestamps):
            diarization.append((labels[i], start, end))

        return diarization
    
    def merge_segments(self, diarization, tolerance=0.5):
        diarization.sort(key=lambda x: x[1])
        merged = []
        current_spk, start, end = diarization[0]

        for spk, s, e in diarization[1:]:
            if spk == current_spk and s - end <= tolerance:
                end = e
            else:
                merged.append((current_spk, start, end))
                current_spk, start, end = spk, s, e

        merged.append((current_spk, start, end))
        return merged
    
    def load_whisper_model(self):
        if self.whisper_model is not None:
            return self.whisper_model
            
        logger.info("Loading Whisper model (large-v3)…")
        self.whisper_model = pipeline(
            "automatic-speech-recognition",
            model="openai/whisper-large-v3",
            device=0 if torch.cuda.is_available() else "cpu",
            chunk_length_s=30,
            batch_size=8
        )
        return self.whisper_model
    
    @contextlib.contextmanager
    def _temp_audio_file(self, audio_np, sr):
        """Context manager for temporary audio files"""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                temp_path = tmp.name
                sf.write(temp_path, audio_np, sr, subtype='PCM_16')
                yield temp_path
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception as e:
                    logger.warning(f"⚠️ Failed to clean up temporary file {temp_path}: {e}")
    
    def transcribe_segment(self, model, audio_path, start, end, sr):
        logger.info(f"    🎙️ Transcribing {start:.2f}s - {end:.2f}s...")
        
        waveform, original_sr = torchaudio.load(audio_path)
        
        if original_sr != sr:
            start_original = int(start * original_sr)
            end_original = int(end * original_sr)
        else:
            start_original = int(start * sr)
            end_original = int(end * sr)
        
        end_original = min(end_original, waveform.shape[1])
        
        segment = waveform[:, start_original:end_original]
        
        if segment.shape[1] == 0:
            return "[No audio in this segment]"
        
        audio_np = segment.squeeze().numpy()
        
        if audio_np.ndim > 1:
            audio_np = audio_np.mean(axis=0)
        
        audio_np = self.preprocess_audio_simple(audio_np, original_sr)
        
        # Use context manager for temporary file
        with self._temp_audio_file(audio_np, original_sr) as temp_file:
            try:
                duration = len(audio_np) / original_sr
                
                result = model(
                    temp_file, 
                    generate_kwargs={
                        "task": "transcribe",
                        "language": "en",
                        "no_repeat_ngram_size": 3,
                        "repetition_penalty": 1.2,
                    }
                )
                
                if isinstance(result, dict):
                    text = result.get("text", "[No transcription]").strip()
                else:
                    text = str(result).strip()
                
                if text and text not in ["[No audio in this segment]", "[Transcription failed]", ""]:
                    text = self.remove_repeated_words(text)
                    logger.info(f"    ✅ Transcription complete (after deduplication)")
                    return text
                else:
                    logger.info(f"    ⚠️ No speech detected")
                    return "[No speech detected]"
                
            except FileNotFoundError as e:
                logger.error(f"    ❌ File not found: {e}")
                return "[Transcription failed - file error]"
            except PermissionError as e:
                logger.error(f"    ❌ Permission denied: {e}")
                return "[Transcription failed - permission error]"
            except (OSError, IOError) as e:
                logger.error(f"    ❌ IO error: {e}")
                return "[Transcription failed - IO error]"
            except Exception as e:
                logger.error(f"    ❌ Transcription error: {e}")
                return "[Transcription failed]"
    
    def get_cluster_embedding(self, embeddings, diarization, speaker_id):
        speaker_embeddings = []
        for i, (spk, start, end) in enumerate(diarization):
            if spk == speaker_id:
                speaker_embeddings.append(embeddings[i])
        
        if not speaker_embeddings:
            return None
        
        mean_emb = np.mean(speaker_embeddings, axis=0)
        normalized_emb = mean_emb / (np.linalg.norm(mean_emb) + 1e-8)
        
        return normalized_emb
    
    def diarize_audio(self, audio_path, agenda="", use_manual_fallback=True, meeting_title=""):
        self.current_progress = 0
        self.progress_message = "Starting diarization..."
        logger.info("🎧 Diarization started with progress tracking")
        
        try:
            self.progress_message = "Converting audio to WAV format..."
            self.current_progress = 5
            wav_path = self.convert_to_wav(audio_path)
            
            self.progress_message = "Loading speaker embedding model..."
            self.current_progress = 15
            model_spk = self.load_embedding_model()
            
            self.progress_message = "Loading voice samples from database..."
            self.current_progress = 25
            voice_embeddings = self.load_voice_samples(model_spk, AUDIO_SAMPLES_DIR)
            
            self.progress_message = "Loading Whisper speech recognition model..."
            self.current_progress = 35
            whisper_model = self.load_whisper_model()

            self.progress_message = "Segmenting audio for speaker identification..."
            self.current_progress = 45
            segments, timestamps, processed_sr = self.segment_audio_for_speaker_id(wav_path)
            
            self.progress_message = "Extracting speaker embeddings..."
            self.current_progress = 55
            embeddings = self.extract_embeddings(model_spk, segments)
            
            self.progress_message = "Performing speaker clustering..."
            self.current_progress = 65
            diarization = self.diarize(embeddings, timestamps)
            
            self.progress_message = "Merging adjacent speaker segments..."
            self.current_progress = 75
            merged = self.merge_segments(diarization)

            self.progress_message = "Transcribing speaker segments..."
            self.current_progress = 80
            logger.info("\n🎧 Final diarization + transcription:\n")
            results = []

            speaker_map = {}
            
            filtered_segments = []
            for spk, start, end in merged:
                duration = end - start
                if duration >= 3.0:
                    filtered_segments.append((spk, start, end))
                else:
                    logger.warning(f"⚠️ Skipping short segment ({duration:.2f}s): Speaker {spk} from {start:.2f}s to {end:.2f}s")

            logger.info(f"\n📊 After filtering: {len(filtered_segments)} segments (removed {len(merged) - len(filtered_segments)} short segments)\n")

            for idx, (spk, start, end) in enumerate(filtered_segments):
                progress = 80 + (idx / len(filtered_segments) * 15) if filtered_segments else 80
                self.current_progress = progress
                self.progress_message = f"Transcribing segment {idx + 1}/{len(filtered_segments)}..."
                
                if spk not in speaker_map:
                    cluster_embedding = self.get_cluster_embedding(embeddings, diarization, spk)
                    
                    if cluster_embedding is not None and voice_embeddings:
                        logger.info(f"\n🔍 Identifying Speaker {spk} (first appears at {start:.2f}s):")
                        identified_name, score = self.identify_speaker(cluster_embedding, voice_embeddings, model_spk)
                        
                        if use_manual_fallback:
                            speaker_map[spk] = identified_name
                            logger.info(f"✅ Auto-assigned: {identified_name} (score: {score:.4f})")
                        else:
                            speaker_map[spk] = identified_name
                            logger.info(f"✅ Auto-assigned: {identified_name} (score: {score:.4f})")
                    else:
                        unknown_counter = len(speaker_map) + 1
                        speaker_map[spk] = f"Unknown_{unknown_counter}"
                        logger.info(f"❓ Speaker {spk} not recognized, labeled as {speaker_map[spk]}")
                
                name = speaker_map[spk]
                
                text = self.transcribe_segment(whisper_model, wav_path, start, end, processed_sr)
                duration = round(end - start, 2)

                logger.info(f"\n{name} ({start:.2f}s - {end:.2f}s | {duration}s): {text}")

                results.append({
                    "speaker": name,
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": duration,
                    "transcript": text
                })

            self.progress_message = "Finalizing diarization..."
            self.current_progress = 95
            
            total_duration = sum(segment["duration"] for segment in results)
            unique_speakers = len(set(segment["speaker"] for segment in results))
            
            output = {
                "agenda": agenda,
                "meeting_title": meeting_title if meeting_title else os.path.basename(audio_path).replace('.wav', ''),
                "audio_file": audio_path,
                "processing_date": datetime.now().isoformat(),
                "total_segments": len(results),
                "total_duration": total_duration,
                "unique_speakers": unique_speakers,
                "transcript": results
            }
            
            self.progress_message = "Diarization completed successfully!"
            self.current_progress = 100
            logger.info("✅ Diarization process completed")
            
            return output
            
        except Exception as e:
            self.progress_message = f"Error: {str(e)}"
            self.current_progress = 0
            logger.error(f"❌ Diarization failed: {e}")
            return None

class BalancedMeetingSummarizer:
    def __init__(self):
        self.company_name = "Asgard Analytics"
        self.company_tagline = "Intelligent Meeting Documentation"
        self._setup_configuration()
        self._setup_folders()
        self._setup_balanced_styles()

    def _setup_configuration(self):
        # Use config instance instead of direct os.getenv
        self.api_key = config.LLM_API_KEY
        self.model = config.LLM_MODEL
        self.base_url = config.LLM_BASE_URL
        self.temperature = config.LLM_TEMPERATURE
        self.max_tokens = config.LLM_MAX_TOKENS
        self.timeout = config.LLM_TIMEOUT
        
        logger.info(f"🤖 LLM Configuration loaded: {self.model}")

    def _setup_folders(self):
        Path('MeetingSummaries').mkdir(exist_ok=True)

    def _setup_balanced_styles(self):
        self.styles = getSampleStyleSheet()

        self.pro_styles = {}

        self.pro_styles['MainTitle'] = ParagraphStyle(
            name='MainTitle',
            parent=self.styles['Title'],
            fontSize=24,
            textColor=colors.HexColor("#0D47A1"),
            spaceAfter=6,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
            leading=28
        )

        self.pro_styles['MeetingTitle'] = ParagraphStyle(
            name='MeetingTitle',
            parent=self.styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor("#1976D2"),
            spaceAfter=10,
            alignment=TA_CENTER,
            fontName='Helvetica',
            leading=18
        )

        self.pro_styles['SectionHeader'] = ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading1'],
            fontSize=14,
            textColor=colors.HexColor("#0D47A1"),
            spaceAfter=6,
            spaceBefore=12,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT
        )

        self.pro_styles['TableHeader'] = ParagraphStyle(
            name='TableHeader',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.white,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            leading=12,
            wordWrap='LTR'
        )

        self.pro_styles['FirstPageTitle'] = ParagraphStyle(
            name='FirstPageTitle',
            parent=self.styles['Title'],
            fontSize=28,
            textColor=colors.HexColor("#0D47A1"),
            spaceAfter=15,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
            leading=32
        )

        self.pro_styles['FirstPageSubtitle'] = ParagraphStyle(
            name='FirstPageSubtitle',
            parent=self.styles['Normal'],
            fontSize=12,
            textColor=colors.HexColor("#666666"),
            spaceAfter=20,
            alignment=TA_CENTER,
            fontName='Helvetica',
            leading=14
        )

    def create_professional_header_footer(self, canvas, doc):
        canvas.saveState()

        header_height = 0.7 * inch
        canvas.setFillColor(colors.HexColor("#0D47A1"))
        canvas.rect(0, doc.pagesize[1] - header_height, doc.pagesize[0], header_height, fill=1, stroke=0)

        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(0.7 * inch, doc.pagesize[1] - 0.45 * inch, self.company_name)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(0.7 * inch, doc.pagesize[1] - 0.65 * inch, self.company_tagline)

        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawRightString(doc.pagesize[0] - 0.7 * inch, doc.pagesize[1] - 0.45 * inch, "MEETING MINUTES")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(doc.pagesize[0] - 0.7 * inch, doc.pagesize[1] - 0.65 * inch, "Professional Summary")

        footer_height = 0.5 * inch
        canvas.setFillColor(colors.HexColor("#42A5F5"))
        canvas.rect(0, 0, doc.pagesize[0], footer_height, fill=1, stroke=0)

        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(doc.pagesize[0] / 2, 0.25 * inch, f"Page {doc.page}")

        canvas.setFont("Helvetica", 8)
        timestamp = datetime.now().strftime("%Y-%m%d %H:%M")
        footer_left = f"© {datetime.now().year} {self.company_name} | Generated: {timestamp}"
        canvas.drawString(0.7 * inch, 0.15 * inch, footer_left)
        canvas.drawRightString(doc.pagesize[0] - 0.7 * inch, 0.15 * inch, "CONFIDENTIAL")

        canvas.restoreState()

    def create_first_page_header_footer(self, canvas, doc):
        canvas.saveState()
        
        canvas.setFillColor(colors.HexColor("#42A5F5"))
        canvas.rect(0, 0, doc.pagesize[0], 0.4 * inch, fill=1, stroke=0)
        
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(doc.pagesize[0] / 2, 0.25 * inch, f"Page {doc.page}")
        
        canvas.setFont("Helvetica", 8)
        canvas.drawString(0.7 * inch, 0.1 * inch, f"© {datetime.now().year} {self.company_name}")
        canvas.drawRightString(doc.pagesize[0] - 0.7 * inch, 0.1 * inch, "CONFIDENTIAL")
        
        canvas.restoreState()

    def _call_llm(self, prompt, max_tokens=4000, temperature=0.7):
        if not self.api_key:
            logger.warning("API key not configured, using fallback summary")
            return None
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/meeting-summarizer",
            "X-Title": "Meeting Summarizer"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            return result['choices'][0]['message']['content']
        except Exception as e:
            logger.error(f"❌ API request failed: {e}")
            return None

    def _extract_agenda_analysis(self, transcripts, agenda):
        all_text = " ".join([t.get('transcript', '') for t in transcripts]).lower()
        agenda_words = set(re.findall(r'\b\w+\b', agenda.lower()))

        relevant_words = [word for word in agenda_words if word in all_text and len(word) > 3]
        relevance_percentage = (len(relevant_words) / len(agenda_words) * 100) if agenda_words else 0

        return {
            'agenda_words': list(agenda_words),
            'relevant_words': relevant_words,
            'relevance_percentage': relevance_percentage
        }

    def _format_transcript_for_analysis(self, transcripts):
        formatted = ""
        for segment in transcripts:
            speaker = segment.get('speaker', 'Unknown')
            transcript = segment.get('transcript', '')
            formatted += f"{speaker}: {transcript}\n\n"
        return formatted

    def _wrap_table_text(self, text, max_length=80):
        if len(text) <= max_length:
            return text
        
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            if len(' '.join(current_line + [word])) <= max_length:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        return '\n'.join(lines)

    def _parse_analysis_sections(self, analysis):
        sections = {}
        current_section = None
        current_content = []

        lines = analysis.split('\n')

        for line in lines:
            line = line.strip()

            if line.startswith('## '):
                if current_section is not None:
                    sections[current_section] = '\n'.join(current_content).strip()
                    current_content = []
                current_section = line[3:].strip()
            elif current_section is not None:
                if line or current_content:
                    current_content.append(line)

        if current_section is not None and current_content:
            sections[current_section] = '\n'.join(current_content).strip()

        return sections

    def _create_balanced_action_items(self, story, content):
        lines = content.split('\n')
        table_data = [['ID', 'Task Description', 'Responsible', 'Deadline', 'Priority']]

        in_table = False
        for line in lines:
            line = line.strip()
            if '|' in line and '---' in line:
                in_table = True
                continue
            elif in_table and '|' in line:
                cells = [cell.strip() for cell in line.split('|') if cell.strip()]
                if len(cells) >= 5:
                    task_desc = self._wrap_table_text(cells[1], max_length=60)
                    responsible = self._wrap_table_text(cells[2], max_length=20)
                    
                    priority = cells[4].lower()
                    if 'high' in priority:
                        priority_color = colors.HexColor("#D32F2F")
                        priority_text = "High"
                    elif 'medium' in priority or 'med' in priority:
                        priority_color = colors.HexColor("#FF9800")
                        priority_text = "Medium"
                    else:
                        priority_color = colors.HexColor("#388E3C")
                        priority_text = "Low"
                    
                    priority_cell = Paragraph(priority_text,
                        ParagraphStyle(
                            name='PriorityStyle',
                            fontName='Helvetica-Bold',
                            fontSize=9,
                            textColor=priority_color,
                            alignment=TA_CENTER
                        ))

                    table_data.append([
                        Paragraph(cells[0], ParagraphStyle(
                            name='TableID',
                            fontName='Helvetica-Bold',
                            fontSize=10,
                            alignment=TA_CENTER
                        )),
                        Paragraph(task_desc, ParagraphStyle(
                            name='TableTask',
                            fontName='Helvetica',
                            fontSize=10,
                            textColor=colors.black,
                            wordWrap='LTR',
                            leading=12
                        )),
                        Paragraph(responsible, ParagraphStyle(
                            name='TableResponsible',
                            fontName='Helvetica',
                            fontSize=10,
                            alignment=TA_CENTER,
                            wordWrap='LTR'
                        )),
                        Paragraph(cells[3], ParagraphStyle(
                            name='TableDeadline',
                            fontName='Helvetica',
                            fontSize=10,
                            alignment=TA_CENTER
                        )),
                        priority_cell
                    ])
            elif in_table and not line:
                break

        if len(table_data) > 1:
            col_widths = [0.5 * inch, 3.5 * inch, 1.2 * inch, 0.8 * inch, 0.7 * inch]
            
            table = Table(table_data, colWidths=col_widths, repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0D47A1")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), "Helvetica-Bold"),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#B2EBF2")),
                ('PADDING', (0, 0), (-1, -1), 6),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FDFE")]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (2, 0), (2, -1), 'CENTER'),
                ('ALIGN', (3, 0), (3, -1), 'CENTER'),
                ('ALIGN', (4, 0), (4, -1), 'CENTER'),
                ('LEADING', (0, 0), (-1, -1), 12),
            ]))
            
            story.append(KeepTogether(table))
        else:
            story.append(Paragraph(content, self.pro_styles['Regular']))

    def _create_balanced_participant_points(self, story, content, transcript_data):
        speakers = list(set(segment.get('speaker', 'Unknown') for segment in transcript_data.get('transcript', [])))

        speaker_stats = []
        for speaker in speakers:
            speaker_segments = [t for t in transcript_data.get('transcript', []) if t.get('speaker') == speaker]
            word_count = sum(len(t.get('transcript', '').split()) for t in speaker_segments)
            segment_count = len(speaker_segments)
            total_words = sum(len(t.get('transcript', '').split()) for t in transcript_data.get('transcript', []))
            percentage = (word_count / total_words * 100) if total_words > 0 else 0
            speaker_stats.append([speaker, str(segment_count), str(word_count), f"{percentage:.1f}%"])

        if speaker_stats:
            stats_data = [['Participant', 'Segments', 'Words', '% of Total']] + speaker_stats
            
            col_widths = [2.0 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch]
            stats_table = Table(stats_data, colWidths=col_widths, repeatRows=1)
            stats_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1976D2")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), "Helvetica-Bold"),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#B2EBF2")),
                ('PADDING', (0, 0), (-1, -1), 6),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FDFE")]),
                ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ]))
            
            story.append(stats_table)
            story.append(Spacer(1, 15))

        lines = content.split('\n')
        current_participant = None
        participant_points = {}

        for line in lines:
            line = line.strip()
            if line.startswith('### '):
                current_participant = line[4:].strip()
                participant_points[current_participant] = []
            elif current_participant and line.startswith('• '):
                point = line[2:].strip()
                if len(point) > 100:
                    point = self._wrap_table_text(point, max_length=100)
                participant_points[current_participant].append(point)

        if participant_points:
            story.append(Paragraph("Key Contributions", ParagraphStyle(
                name='ContributionsTitle',
                fontName='Helvetica-Bold',
                fontSize=12,
                textColor=colors.HexColor("#0D47A1"),
                spaceAfter=8
            )))

            for participant, points in participant_points.items():
                if len(participant) > 40:
                    participant = self._wrap_table_text(participant, max_length=40)
                
                story.append(Paragraph(f"<b>{participant}</b>", ParagraphStyle(
                    name='ParticipantName',
                    fontName='Helvetica-Bold',
                    fontSize=11,
                    textColor=colors.HexColor("#0D47A1"),
                    spaceAfter=2,
                    spaceBefore=6
                )))

                for point in points[:3]:
                    story.append(Paragraph(f"• {point}", ParagraphStyle(
                        name='KeyPoint',
                        parent=self.styles['Normal'],
                        fontSize=10,
                        textColor=colors.HexColor("#333333"),
                        leftIndent=15,
                        spaceAfter=3,
                        fontName='Helvetica',
                        leading=12,
                        wordWrap='LTR'
                    )))

                story.append(Spacer(1, 8))
        else:
            for speaker in speakers[:10]:
                story.append(Paragraph(f"<b>{speaker}</b>", ParagraphStyle(
                    name='ParticipantName',
                    fontName='Helvetica-Bold',
                    fontSize=11,
                    textColor=colors.HexColor("#0D47A1"),
                    spaceAfter=2
                )))
                story.append(Paragraph("• Contributed to key discussions", ParagraphStyle(
                    name='KeyPoint',
                    parent=self.styles['Normal'],
                    fontSize=10,
                    textColor=colors.HexColor("#333333"),
                    leftIndent=15,
                    spaceAfter=3,
                    fontName='Helvetica',
                    leading=12,
                    wordWrap='LTR'
                )))
                story.append(Spacer(1, 5))

    def _create_agenda_analysis_section(self, story, content):
        lines = content.split('\n')
        topics_covered = []
        topics_not_covered = []
        current_section = None

        for line in lines:
            line = line.strip()
            if line.startswith('### Topics Covered'):
                current_section = 'covered'
            elif line.startswith('### Topics Not Covered'):
                current_section = 'not_covered'
            elif line.startswith('### Agenda Adherence'):
                current_section = 'assessment'
            elif line.startswith('• ') and current_section == 'covered':
                topic = line[2:].strip()
                if len(topic) > 100:
                    topic = self._wrap_table_text(topic, max_length=100)
                topics_covered.append(topic)
            elif line.startswith('• ') and current_section == 'not_covered':
                topic = line[2:].strip()
                if len(topic) > 100:
                    topic = self._wrap_table_text(topic, max_length=100)
                topics_not_covered.append(topic)

        if topics_covered:
            story.append(Paragraph("Topics Discussed", ParagraphStyle(
                name='TopicsTitle',
                fontName='Helvetica-Bold',
                fontSize=12,
                textColor=colors.HexColor("#0D47A1"),
                spaceAfter=6
            )))
            for topic in topics_covered[:5]:
                story.append(Paragraph(f"• {topic}", ParagraphStyle(
                    name='KeyPoint',
                    parent=self.styles['Normal'],
                    fontSize=10,
                    textColor=colors.HexColor("#333333"),
                    leftIndent=15,
                    spaceAfter=3,
                    fontName='Helvetica',
                    leading=12,
                    wordWrap='LTR'
                )))
            story.append(Spacer(1, 10))

        if topics_not_covered:
            story.append(Paragraph("Topics Not Discussed", ParagraphStyle(
                name='TopicsNotTitle',
                fontName='Helvetica-Bold',
                fontSize=12,
                textColor=colors.HexColor("#FF9800"),
                spaceAfter=6
            )))
            for topic in topics_not_covered[:3]:
                story.append(Paragraph(f"• {topic}", ParagraphStyle(
                    name='KeyPoint',
                    parent=self.styles['Normal'],
                    fontSize=10,
                    textColor=colors.HexColor("#333333"),
                    leftIndent=15,
                    spaceAfter=3,
                    fontName='Helvetica',
                    leading=12,
                    wordWrap='LTR'
                )))
            story.append(Spacer(1, 10))

        for line in lines:
            line = line.strip()
            if line and not line.startswith('###') and not line.startswith('•'):
                if 'agenda' in line.lower() or 'adherence' in line.lower() or 'focused' in line.lower():
                    if len(line) > 120:
                        line = self._wrap_table_text(line, max_length=120)
                    story.append(Paragraph(line, ParagraphStyle(
                        name='Regular',
                        parent=self.styles['Normal'],
                        fontSize=10,
                        textColor=colors.black,
                        spaceAfter=4,
                        fontName='Helvetica',
                        leading=12,
                        wordWrap='LTR'
                    )))
                    break

    def _add_balanced_text_content(self, story, content):
        lines = content.split('\n')

        for line in lines:
            line = line.strip()
            if line:
                if line.startswith('• '):
                    bullet_text = line[2:]
                    if len(bullet_text) > 100:
                        bullet_text = self._wrap_table_text(bullet_text, max_length=100)
                    story.append(Paragraph(f"• {bullet_text}", ParagraphStyle(
                        name='KeyPoint',
                        parent=self.styles['Normal'],
                        fontSize=10,
                        textColor=colors.HexColor("#333333"),
                        leftIndent=15,
                        spaceAfter=3,
                        fontName='Helvetica',
                        leading=12,
                        wordWrap='LTR'
                    )))
                elif line.startswith('### '):
                    story.append(Paragraph(line[4:], ParagraphStyle(
                        name='Subsection',
                        fontName='Helvetica-Bold',
                        fontSize=12,
                        textColor=colors.HexColor("#0D47A1"),
                        spaceAfter=4
                    )))
                    story.append(Spacer(1, 5))
                elif ':' in line and len(line) < 120:
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        story.append(Paragraph(f"<b>{parts[0]}:</b> {parts[1]}", ParagraphStyle(
                            name='Regular',
                            parent=self.styles['Normal'],
                            fontSize=10,
                            textColor=colors.black,
                            spaceAfter=4,
                            fontName='Helvetica',
                            leading=12,
                            wordWrap='LTR'
                        )))
                else:
                    if len(line) > 120:
                        line = self._wrap_table_text(line, max_length=120)
                    story.append(Paragraph(line, ParagraphStyle(
                        name='Regular',
                        parent=self.styles['Normal'],
                        fontSize=10,
                        textColor=colors.black,
                        spaceAfter=4,
                        fontName='Helvetica',
                        leading=12,
                        wordWrap='LTR'
                    )))
                story.append(Spacer(1, 3))

    def _create_first_page(self, story, meeting_id, transcript_data, agenda_analysis, meeting_title, agenda):
        story.append(Spacer(1, 0.8 * inch))
        
        story.append(Paragraph("MEETING MINUTES", self.pro_styles['FirstPageTitle']))
        story.append(Spacer(1, 0.2 * inch))
        
        story.append(Paragraph(self.company_name, ParagraphStyle(
            name='CompanyFirst',
            fontName='Helvetica-Bold',
            fontSize=14,
            textColor=colors.HexColor("#0D47A1"),
            alignment=TA_CENTER,
            spaceAfter=4
        )))
        story.append(Paragraph(self.company_tagline, ParagraphStyle(
            name='TaglineFirst',
            fontName='Helvetica',
            fontSize=10,
            textColor=colors.HexColor("#666666"),
            alignment=TA_CENTER,
            spaceAfter=0.4 * inch
        )))
        
        story.append(Table(
            [[Paragraph(meeting_title, ParagraphStyle(
                name='AgendaFirst',
                fontName='Helvetica-Bold',
                fontSize=16,
                textColor=colors.white,
                alignment=TA_CENTER,
                leading=18
            ))]],
            colWidths=[6.5 * inch],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (0, 0), colors.HexColor("#0D47A1")),
                ('BOX', (0, 0), (0, 0), 1, colors.HexColor("#004D40")),
                ('PADDING', (0, 0), (0, 0), 12),
                ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
            ])
        ))
        
        story.append(Spacer(1, 0.4 * inch))
        
        total_duration = transcript_data.get('total_duration', 0)
        minutes = int(total_duration // 60)
        seconds = int(total_duration % 60)
        if minutes > 0:
            duration_formatted = f"{minutes} minutes {seconds} seconds"
        else:
            duration_formatted = f"{seconds} seconds"
        
        participants = transcript_data.get('unique_speakers', 0)
        total_words = sum(len(segment.get('transcript', '').split()) for segment in transcript_data.get('transcript', []))
        relevance = agenda_analysis.get('relevance_percentage', 0)
        
        if relevance > 70:
            relevance_color = colors.HexColor("#388E3C")
            relevance_status = "Excellent"
        elif relevance > 40:
            relevance_color = colors.HexColor("#FF9800")
            relevance_status = "Good"
        else:
            relevance_color = colors.HexColor("#D32F2F")
            relevance_status = "Low"
        
        details_data = [
            ['Meeting Details', ''],
            ['Meeting ID:', meeting_id],
            ['Date:', datetime.now().strftime('%A, %B %d, %Y')],
            ['Time:', datetime.now().strftime('%I:%M %p')],
            ['Duration:', duration_formatted],
            ['Participants:', str(participants)],
            ['Word Count:', f"{total_words:,}"],
            ['Agenda Relevance:', f"{relevance:.1f}% ({relevance_status})"],
        ]
        
        details_table = Table(details_data, colWidths=[2.0 * inch, 4.5 * inch])
        details_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (1, 0), colors.HexColor("#0D47A1")),
            ('TEXTCOLOR', (0, 0), (1, 0), colors.white),
            ('FONTNAME', (0, 0), (1, 0), "Helvetica-Bold"),
            ('FONTSIZE', (0, 0), (1, 0), 12),
            ('ALIGN', (0, 0), (1, 0), 'CENTER'),
            ('PADDING', (0, 0), (1, 0), 10),
            
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor("#E3F2FD")),
            ('FONTNAME', (0, 1), (0, -1), "Helvetica-Bold"),
            ('FONTSIZE', (0, 1), (0, -1), 10),
            ('ALIGN', (0, 1), (0, -1), 'RIGHT'),
            ('PADDING', (0, 1), (0, -1), 8),
            
            ('FONTNAME', (1, 1), (1, -1), "Helvetica"),
            ('FONTSIZE', (1, 1), (1, -1), 10),
            ('ALIGN', (1, 1), (1, -1), 'LEFT'),
            ('PADDING', (1, 1), (1, -1), 8),
            ('LEFTPADDING', (1, 1), (1, -1), 12),
            
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#B2EBF2")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            
            ('TEXTCOLOR', (1, 7), (1, 7), relevance_color),
            ('FONTNAME', (1, 7), (1, 7), "Helvetica-Bold"),
        ]))
        
        story.append(details_table)
        story.append(Spacer(1, 0.4 * inch))
        
        speakers = list(set(segment.get('speaker', 'Unknown') for segment in transcript_data.get('transcript', [])))
        speakers_text = ", ".join(speakers)
        story.append(Paragraph("Participants", ParagraphStyle(
            name='ParticipantsTitle',
            fontName='Helvetica-Bold',
            fontSize=11,
            textColor=colors.HexColor("#0D47A1"),
            alignment=TA_LEFT,
            spaceAfter=6
        )))
        story.append(Paragraph(speakers_text, ParagraphStyle(
            name='ParticipantsList',
            fontName='Helvetica',
            fontSize=10,
            textColor=colors.HexColor("#333333"),
            alignment=TA_LEFT,
            spaceAfter=0.3 * inch
        )))
        
        story.append(Table(
            [[Paragraph("CONFIDENTIAL BUSINESS DOCUMENT", ParagraphStyle(
                name='ConfidentialFirst',
                fontName='Helvetica-Bold',
                fontSize=11,
                textColor=colors.HexColor("#D32F2F"),
                alignment=TA_CENTER
            ))]],
            colWidths=[6.5 * inch],
            style=TableStyle([
                ('BACKGROUND', (0, 0), (0, 0), colors.HexColor("#FFEBEE")),
                ('BOX', (0, 0), (0, 0), 1, colors.HexColor("#D32F2F")),
                ('PADDING', (0, 0), (0, 0), 10),
                ('VALIGN', (0, 0), (0, 0), 'MIDDLE'),
            ])
        ))
        
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("For internal distribution only • Do not share without authorization", 
                             ParagraphStyle(
                                 name='NoticeFirst',
                                 fontName='Helvetica',
                                 fontSize=8,
                                 textColor=colors.HexColor("#666666"),
                                 alignment=TA_CENTER
                             )))
        
        story.append(PageBreak())

    def _add_balanced_sections(self, story, sections, transcript_data):
        section_order = [
            "1. EXECUTIVE SUMMARY",
            "2. KEY DECISIONS & OUTCOMES",
            "3. PARTICIPANT KEY POINTS",
            "4. ACTION ITEMS",
            "5. AGENDA ANALYSIS",
            "6. KEY METRICS & DATA POINTS",
            "7. NEXT STEPS & RECOMMENDATIONS"
        ]

        for section_title in section_order:
            if section_title in sections:
                story.append(Paragraph(section_title, self.pro_styles['SectionHeader']))
                story.append(Spacer(1, 8))

                content = sections[section_title]

                if section_title == "4. ACTION ITEMS":
                    self._create_balanced_action_items(story, content)
                elif section_title == "3. PARTICIPANT KEY POINTS":
                    self._create_balanced_participant_points(story, content, transcript_data)
                elif section_title == "5. AGENDA ANALYSIS":
                    self._create_agenda_analysis_section(story, content)
                else:
                    self._add_balanced_text_content(story, content)

                story.append(Spacer(1, 15))

    def generate_balanced_analysis(self, transcript_data, meeting_title, agenda):
        logger.info("🤖 Generating balanced meeting analysis...")

        if not transcript_data or not transcript_data.get('transcript'):
            logger.warning("⚠️ No valid transcript content available")
            return None, None, None

        transcripts = transcript_data.get('transcript', [])
        
        agenda_analysis = self._extract_agenda_analysis(transcripts, agenda)

        meeting_id = f"MEET_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        logger.info(f"📅 Meeting ID: {meeting_id}")

        analysis = self._generate_balanced_llm_analysis(transcripts, agenda, transcript_data, meeting_id, agenda_analysis)

        return analysis, meeting_id, agenda_analysis

    def _generate_balanced_llm_analysis(self, transcripts, agenda, transcript_data, meeting_id, agenda_analysis):
        formatted_transcript = self._format_transcript_for_analysis(transcripts)

        speakers = list(set(segment.get('speaker', 'Unknown') for segment in transcripts))
        
        total_duration = transcript_data.get('total_duration', 0)
        minutes = int(total_duration // 60)
        seconds = int(total_duration % 60)
        if minutes > 0:
            duration_formatted = f"{minutes} minutes {seconds} seconds"
        else:
            duration_formatted = f"{seconds} seconds"

        prompt = f"""Create a PROFESSIONAL but CONCISE meeting summary with balanced detail.

MEETING INFORMATION:
- Title: {transcript_data.get('meeting_title', 'Meeting')}
- Agenda: {agenda}
- Participants: {', '.join(speakers)}
- Duration: {duration_formatted}
- Total Segments: {len(transcripts)}
- Agenda Relevance: {agenda_analysis['relevance_percentage']:.1f}%

YOUR TASK: Provide a comprehensive yet concise analysis with these EXACT sections:

## 1. EXECUTIVE SUMMARY
[3-4 sentences summarizing the entire meeting, objectives, and key outcomes]

## 2. KEY DECISIONS & OUTCOMES
[List 3-5 most important decisions made, format as bullet points]

## 3. PARTICIPANT KEY POINTS
[For each participant, list their 2-3 key contributions]
### [Participant Name]
• [Key contribution 1]
• [Key contribution 2]
[Optional: • Key contribution 3]

## 4. ACTION ITEMS
[Create a table with these columns]
| ID | Task Description | Responsible Person | Deadline | Priority |
|---|---|---|---|---|
| A1 | [Clear task description] | [Name] | [Date] | [High/Medium/Low] |

## 5. AGENDA ANALYSIS
### Topics Covered
[List bullet points of agenda items that were discussed]

### Topics Not Covered  
[List bullet points of agenda items that were NOT discussed]

### Agenda Adherence Assessment
[Brief assessment of how well the meeting stuck to the agenda]

## 6. KEY METRICS & DATA POINTS
[List all important numbers, percentages, dates, and metrics mentioned]

## 7. NEXT STEPS & RECOMMENDATIONS
[List 3-5 actionable next steps]

IMPORTANT INSTRUCTIONS:
1. Be professional but concise
2. Use bullet points, not long paragraphs
3. Include specific names and details from the transcript
4. Format tables clearly
5. Keep each section focused and to the point
6. Maximum 1 page of content (excluding title)

MEETING TRANSCRIPT:
{formatted_transcript}

Now provide the balanced professional summary in the exact structure above."""

        logger.info("🧠 Analyzing meeting with balanced prompt...")
        response = self._call_llm(prompt, max_tokens=4500, temperature=0.7)

        if not response:
            logger.info("❌ AI analysis failed, using fallback")
            return self._generate_fallback_analysis(transcripts, agenda, transcript_data, meeting_id, agenda_analysis)

        return response

    def _generate_fallback_analysis(self, transcripts, agenda, transcript_data, meeting_id, agenda_analysis):
        speakers = list(set(segment.get('speaker', 'Unknown') for segment in transcripts))

        participant_points = {}
        for speaker in speakers:
            speaker_texts = [t.get('transcript', '') for t in transcripts if t.get('speaker') == speaker]
            key_points = []
            for text in speaker_texts[:3]:
                sentences = text.split('. ')
                if sentences and len(sentences[0]) > 20:
                    key_points.append(sentences[0][:100] + "...")
            participant_points[speaker] = key_points[:3]

        analysis = f"""## 1. EXECUTIVE SUMMARY
The meeting focused on {agenda}. Key discussions included project updates, resource allocation, and strategic planning. The meeting was productive with clear decisions made for next steps.

## 2. KEY DECISIONS & OUTCOMES
• Review and approve project timeline adjustments
• Allocate additional budget for critical resources
• Schedule follow-up meeting for detailed planning
• Assign task owners for action items

## 3. PARTICIPANT KEY POINTS
"""

        for speaker, points in participant_points.items():
            analysis += f"\n### {speaker}\n"
            for point in points:
                analysis += f"• {point}\n"

        analysis += f"""
## 4. ACTION ITEMS
| ID | Task Description | Responsible Person | Deadline | Priority |
|---|---|---|---|---|
| A1 | Review meeting minutes | All participants | {datetime.now().strftime('%Y-%m-%d')} | Medium |
| A2 | Prepare detailed project plan | Project Lead | Next week | High |
| A3 | Allocate resources | Resource Manager | 3 days | High |

## 5. AGENDA ANALYSIS
### Topics Covered
• General discussion of {agenda}
• Resource planning
• Timeline review

### Topics Not Covered  
• Detailed technical specifications
• Budget approval process

### Agenda Adherence Assessment
Approximately {agenda_analysis['relevance_percentage']:.1f}% of the discussion was directly related to the agenda. The meeting stayed reasonably focused on key topics.

## 6. KEY METRICS & DATA POINTS
• Meeting duration: {transcript_data.get('total_duration', 0):.1f} seconds
• Participants: {len(speakers)}
• Total segments: {len(transcripts)}
• Agenda relevance: {agenda_analysis['relevance_percentage']:.1f}%

## 7. NEXT STEPS & RECOMMENDATIONS
• Schedule follow-up meeting within 1 week
• Distribute meeting minutes to all participants
• Begin implementation of approved decisions
• Monitor action item progress regularly"""

        return analysis

    def create_balanced_pdf(self, analysis, meeting_id, transcript_data, agenda_analysis, meeting_title, agenda, output_path):
        logger.info(f"📊 Creating balanced PDF for {meeting_id}...")

        try:
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=0.7 * inch,
                leftMargin=0.7 * inch,
                topMargin=1.0 * inch,
                bottomMargin=0.8 * inch
            )

            story = []

            self._create_first_page(story, meeting_id, transcript_data, agenda_analysis, meeting_title, agenda)

            sections = self._parse_analysis_sections(analysis)
            self._add_balanced_sections(story, sections, transcript_data)

            def on_first_page(canvas, doc):
                self.create_first_page_header_footer(canvas, doc)
            
            def on_later_pages(canvas, doc):
                self.create_professional_header_footer(canvas, doc)
            
            doc.build(story, onFirstPage=on_first_page, onLaterPages=on_later_pages)

            logger.info(f"✅ Balanced PDF created: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Error creating PDF: {e}")
            traceback.print_exc()
            return None

def create_enhanced_transcript_pdf(transcript_data, meeting_title, output_path):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        
        doc = SimpleDocTemplate(output_path, pagesize=letter, 
                                rightMargin=0.5*inch, leftMargin=0.5*inch,
                                topMargin=0.5*inch, bottomMargin=0.5*inch)
        
        story = []
        styles = getSampleStyleSheet()
        
        title_style = styles['Title']
        title_style.fontSize = 16
        title_style.textColor = colors.HexColor('#0D47A1')
        
        meeting_title = clean_meeting_title_for_display(meeting_title)
        story.append(Paragraph(f"Meeting Transcript: {meeting_title}", title_style))
        story.append(Spacer(1, 0.2*inch))
        
        table_data = [['Speaker', 'Time Range', 'Duration (s)', 'Transcript']]
        
        for segment in transcript_data.get('transcript', []):
            speaker = segment.get('speaker', 'Unknown')
            start = segment.get('start', 0)
            end = segment.get('end', 0)
            duration = segment.get('duration', 0)
            transcript = segment.get('transcript', '')
            
            if not transcript.strip() or transcript.strip() in ['[No speech detected]', '[Transcription failed]']:
                continue
            
            transcript = EnhancedAudioDiarizer.remove_repeated_words(transcript)
            
            if len(transcript) > 150:
                words = transcript.split()
                lines = []
                current_line = []
                
                for word in words:
                    if len(' '.join(current_line + [word])) <= 100:
                        current_line.append(word)
                    else:
                        lines.append(' '.join(current_line))
                        current_line = [word]
                
                if current_line:
                    lines.append(' '.join(current_line))
                
                transcript = '\n'.join(lines)
            
            table_data.append([
                speaker,
                f"{start:.1f}s - {end:.1f}s",
                f"{duration:.1f}",
                transcript
            ])
        
        table = Table(table_data, colWidths=[1.2*inch, 1.5*inch, 1*inch, 4.3*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0D47A1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('WORDWRAP', (3, 1), (3, -1), True),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#E3F2FD')]),
            ('LEADING', (3, 1), (3, -1), 12),
        ]))
        
        story.append(table)
        
        story.append(Spacer(1, 0.3*inch))
        total_segments = len(transcript_data.get('transcript', []))
        total_duration = transcript_data.get('total_duration', 0)
        unique_speakers = transcript_data.get('unique_speakers', 0)
        
        stats_text = f"<b>Summary:</b> {total_segments} segments, {total_duration/60:.1f} minutes, {unique_speakers} speakers"
        stats_style = ParagraphStyle(
            name='StatsStyle',
            fontName='Helvetica-Bold',
            fontSize=10,
            textColor=colors.HexColor('#0D47A1'),
            alignment=TA_CENTER
        )
        story.append(Paragraph(stats_text, stats_style))
        
        doc.build(story)
        logger.info(f"✅ Simplified transcript PDF created: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"❌ Error creating transcript PDF: {e}")
        traceback.print_exc()
        return None

class EmailAutomator:
    def __init__(self):
        self.config = self._load_config()
    
    def _load_config(self):
        config_dict = {
            "LLM": {
                "api_key": config.LLM_API_KEY,
                "model": config.LLM_MODEL,
                "base_url": config.LLM_BASE_URL,
                "temperature": config.LLM_TEMPERATURE,
                "max_tokens": config.LLM_MAX_TOKENS,
                "timeout": config.LLM_TIMEOUT
            },
            "EMAIL": {
                "sender": config.EMAIL_SENDER,
                "password": config.EMAIL_PASSWORD,
                "smtp_server": config.EMAIL_SMTP_SERVER,
                "smtp_port": config.EMAIL_SMTP_PORT,
                "use_tls": config.EMAIL_USE_TLS,
                "default_subject": config.EMAIL_DEFAULT_SUBJECT,
                "default_body": config.EMAIL_DEFAULT_BODY
            }
        }
        
        if not config_dict["EMAIL"]["sender"] or not config_dict["EMAIL"]["password"]:
            logger.error("❌ Email configuration incomplete in .env file")
            logger.error("   Please set EMAIL_SENDER and EMAIL_PASSWORD in .env file")
        
        return config_dict
    
    def send_email(self, recipients: List[str], subject: str, body: str, attachments: List[str] = None) -> bool:
        try:
            sender_email = self.config["EMAIL"]["sender"]
            password = self.config["EMAIL"]["password"]
            smtp_server = self.config["EMAIL"]["smtp_server"]
            smtp_port = self.config["EMAIL"]["smtp_port"]
            use_tls = self.config["EMAIL"]["use_tls"]
            
            if not sender_email or not password:
                logger.error("❌ Email credentials not configured in .env file")
                return False
            
            msg = MIMEMultipart()
            msg['From'] = sender_email
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            
            if attachments:
                for file_path in attachments:
                    if file_path.lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
                        logger.info(f"⏭️  Skipping video file (too large): {file_path}")
                        continue
                    
                    if not os.path.exists(file_path):
                        logger.warning(f"⚠️  Attachment file not found: {file_path}")
                        continue
                    
                    try:
                        with open(file_path, "rb") as attachment:
                            part = MIMEBase('application', 'octet-stream')
                            part.set_payload(attachment.read())
                            encoders.encode_base64(part)
                            part.add_header(
                                'Content-Disposition',
                                f'attachment; filename="{os.path.basename(file_path)}"'
                            )
                            msg.attach(part)
                            logger.info(f"✅ Attached file: {os.path.basename(file_path)}")
                    except Exception as e:
                        logger.warning(f"⚠️  Failed to attach file {file_path}: {e}")
            
            server = smtplib.SMTP(smtp_server, smtp_port)
            if use_tls:
                server.starttls()
            server.login(sender_email, password)
            server.send_message(msg)
            server.quit()
            
            logger.info(f"✅ Email sent successfully to {len(recipients)} recipients")
            return True
            
        except Exception as e:
            logger.error(f"❌ Email sending failed: {e}")
            return False

class SimpleAudioRecorder:
    def __init__(self, sr=16000):
        self.sr = sr
        self.is_recording = False
        self.stream = None
        self.audio_buffer = []
        self.meeting_audio_file = None
        
    def start_recording(self, meeting_id, meeting_folder_path):
        self.is_recording = True
        self.audio_buffer = []
        
        if meeting_folder_path and os.path.exists(meeting_folder_path):
            os.makedirs(meeting_folder_path, exist_ok=True)
            
            folder_name = os.path.basename(meeting_folder_path)
            self.meeting_audio_file = os.path.join(meeting_folder_path, f"{folder_name}_audio.wav")
        else:
            logger.error(f"❌ Meeting folder path is invalid: {meeting_folder_path}")
            return False
        
        def audio_callback(indata, frames, time, status):
            if self.is_recording:
                audio_chunk = indata.copy().flatten()
                self.audio_buffer.append(audio_chunk)
        
        self.stream = sd.InputStream(
            callback=audio_callback,
            channels=1,
            samplerate=self.sr,
            blocksize=2048,
            dtype='float32'
        )
        self.stream.start()
        logger.info(f"🎤 Audio recording started for meeting: {meeting_id}")
        return True
    
    def stop_recording(self):
        if self.stream:
            self.is_recording = False
            self.stream.stop()
            self.stream.close()
            
            if self.audio_buffer and self.meeting_audio_file:
                try:
                    full_audio = np.concatenate(self.audio_buffer, axis=0)
                    sf.write(self.meeting_audio_file, full_audio, self.sr)
                    logger.info(f"💾 Audio saved: {self.meeting_audio_file}")
                    return self.meeting_audio_file
                except Exception as e:
                    logger.error(f"❌ Failed to save audio: {e}")
                    return None
                    
        return None

class AdvancedAttendanceSystem:
    def __init__(self):
        self.face_model = None
        self.yolo_model = None
        self.known_embeddings = []
        self.known_names = []
        self.all_required_persons = set()
        self.tracked_persons = {}
        self.is_running = False
        self.cap = None
        self.current_camera_source = 0
        self.session_start_time = datetime.now()
        self.tracking_mode = True
        self.ws = None
        self.camera_zoom = 1.0
        self.person_attendance_state = {}
        self.attendance_session_count = 0
    
    def initialize_models(self):
        try:
            if torch.cuda.is_available():
                providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
                logger.info("[INFO] Using GPU for face recognition")
            else:
                providers = ['CPUExecutionProvider']
                logger.info("[INFO] Using CPU for face recognition")
            
            # Use config.FACE_MODEL_CONFIG
            self.face_model = insightface.app.FaceAnalysis(
                allowed_modules=config.FACE_MODEL_CONFIG['allowed_modules'],
                providers=providers
            )
            self.face_model.prepare(ctx_id=0 if torch.cuda.is_available() else -1)
            logger.info("[SUCCESS] Face recognition model initialized")
            
            try:
                self.yolo_model = YOLO('yolov8n.pt')
                if torch.cuda.is_available():
                    self.yolo_model.to('cuda')
                    logger.info("[SUCCESS] YOLO person tracking model initialized on GPU")
                else:
                    logger.info("[SUCCESS] YOLO person tracking model initialized on CPU")
            except Exception as e:
                logger.error(f"[ERROR] Failed to initialize YOLO: {e}")
                self.yolo_model = YOLO('yolov8n.pt')
                logger.info("[INFO] Using fallback YOLO model")
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to initialize models: {e}")
            self.face_model = insightface.app.FaceAnalysis()
            self.face_model.prepare(ctx_id=0)
            logger.info("[INFO] Using fallback face model")
    
    def load_known_faces(self):
        self.known_embeddings, self.known_names = [], []
        self.all_required_persons.clear()
        self.tracked_persons.clear()
        self.person_attendance_state.clear()
        
        if not os.path.exists(KNOWN_FACES_DIR):
            os.makedirs(KNOWN_FACES_DIR)
            logger.info(f"[INFO] Created directory: {KNOWN_FACES_DIR}")
            return
            
        for person_name in os.listdir(KNOWN_FACES_DIR):
            person_dir = os.path.join(KNOWN_FACES_DIR, person_name)
            if not os.path.isdir(person_dir):
                continue
            
            self.all_required_persons.add(person_name)
                
            for img_name in os.listdir(person_dir):
                if not img_name.lower().endswith(('.jpg', '.png', '.jpeg')):
                    continue
                    
                img_path = os.path.join(person_dir, img_name)
                img = cv2.imread(img_path)
                if img is None:
                    continue
                    
                faces = self.face_model.get(img)
                if faces:
                    self.known_embeddings.append(faces[0].embedding)
                    self.known_names.append(person_name)
                    logger.info(f"[LOADED] Face embedding for: {person_name}")
                    
        logger.info(f"[SUMMARY] Loaded {len(self.known_embeddings)} known faces from {len(self.all_required_persons)} persons.")
    
    def cosine_similarity(self, a, b):
        a_norm = np.linalg.norm(a)
        b_norm = np.linalg.norm(b)
        if a_norm == 0 or b_norm == 0:
            return 0
        return np.dot(a, b) / (a_norm * b_norm)
    
    def is_face_inside_person(self, face_bbox, person_bbox):
        fx1, fy1, fx2, fy2 = face_bbox
        px1, py1, px2, py2 = person_bbox
        
        face_center_x = (fx1 + fx2) / 2
        face_center_y = (fy1 + fy2) / 2
        
        return (px1 <= face_center_x <= px2) and (py1 <= face_center_y <= py2)
    
    def mark_person_permanently_present(self, track_id, name, timestamp):
        if name != "Unknown":
            if track_id not in self.tracked_persons:
                self.tracked_persons[track_id] = {
                    'name': name,
                    'first_seen_time': timestamp,
                    'last_seen_time': timestamp,
                    'total_duration': 0,
                    'current_session_start': timestamp,
                    'in_frame': True,
                    'recognized': True,
                    'permanently_present': True,
                    'sessions_present': {self.attendance_session_count},
                    'status': 'Present'
                }
                logger.info(f"[PRESENT] {name} detected at {timestamp.strftime('%H:%M:%S')}")
            else:
                person_data = self.tracked_persons[track_id]
                person_data['name'] = name
                person_data['recognized'] = True
                person_data['permanently_present'] = True
                
                if self.attendance_session_count not in person_data['sessions_present']:
                    person_data['sessions_present'].add(self.attendance_session_count)
                
                if person_data['current_session_start'] is None:
                    person_data['current_session_start'] = timestamp
    
    def start_attendance_session(self):
        self.attendance_session_count += 1
        logger.info(f"📊 Starting attendance session #{self.attendance_session_count}")
    
    def update_presence_time(self, track_id, current_time, in_frame_now):
        if track_id in self.tracked_persons and self.tracked_persons[track_id]['recognized']:
            person_data = self.tracked_persons[track_id]
            
            if in_frame_now and person_data['current_session_start'] is None:
                person_data['current_session_start'] = current_time
                logger.info(f"[ENTER] {person_data['name']} entered at {current_time.strftime('%H:%M:%S')}")
            
            elif in_frame_now and person_data['current_session_start'] is not None:
                session_duration = (current_time - person_data['current_session_start']).total_seconds()
                person_data['total_duration'] += session_duration
                person_data['current_session_start'] = current_time
            
            elif not in_frame_now and person_data['current_session_start'] is not None:
                session_duration = (current_time - person_data['current_session_start']).total_seconds()
                person_data['total_duration'] += session_duration
                person_data['current_session_start'] = None
                logger.info(f"[EXIT] {person_data['name']} left at {current_time.strftime('%H:%M:%S')}")
            
            person_data['last_seen_time'] = current_time
            person_data['in_frame'] = in_frame_now
    
    def process_frame_with_tracking(self, frame):
        current_time = datetime.now()
        
        if self.camera_zoom != 1.0:
            height, width = frame.shape[:2]
            new_height, new_width = int(height * self.camera_zoom), int(width * self.camera_zoom)
            frame = cv2.resize(frame, (new_height, new_width))
        
        annotated_frame = frame.copy()
        
        if not self.tracking_mode:
            return annotated_frame, 0, 0, 0
        
        current_track_ids = set()
        
        try:
            if self.yolo_model:
                if torch.cuda.is_available():
                    device = 'cuda'
                else:
                    device = 'cpu'
                
                results = self.yolo_model.track(frame, persist=True, conf=0.5, classes=[0], 
                                               verbose=False, device=device)
                
                if results and results[0].boxes.id is not None:
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    track_ids = results[0].boxes.id.cpu().numpy().astype(int)
                    
                    faces = self.face_model.get(frame)
                    
                    for box, track_id in zip(boxes, track_ids):
                        x1, y1, x2, y2 = map(int, box)
                        current_track_ids.add(track_id)
                        
                        if track_id not in self.tracked_persons:
                            self.tracked_persons[track_id] = {
                                'name': "Unknown",
                                'first_seen_time': current_time,
                                'last_seen_time': current_time,
                                'total_duration': 0,
                                'current_session_start': current_time,
                                'in_frame': True,
                                'recognized': False,
                                'permanently_present': False,
                                'sessions_present': set(),
                                'status': 'Unknown'
                            }
                        
                        person_data = self.tracked_persons[track_id]
                        person_data['in_frame'] = True
                        
                        if not person_data['recognized'] and faces:
                            for face in faces:
                                face_bbox = face.bbox.astype(int)
                                if self.is_face_inside_person(face_bbox, (x1, y1, x2, y2)):
                                    embedding = face.embedding
                                    name = "Unknown"
                                    max_sim = SIMILARITY_THRESHOLD
                                    
                                    for known_emb, known_name in zip(self.known_embeddings, self.known_names):
                                        sim = self.cosine_similarity(embedding, known_emb)
                                        if sim > max_sim:
                                            max_sim = sim
                                            name = known_name
                                    
                                    if name != "Unknown":
                                        self.mark_person_permanently_present(track_id, name, current_time)
                                        break
                        
                        if person_data['recognized']:
                            self.update_presence_time(track_id, current_time, True)
                        
                        if person_data['recognized']:
                            color = (0, 255, 0)
                        else:
                            color = (255, 165, 0)
                        
                        cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
                        
                        label = f"{person_data['name']}"
                        cv2.putText(annotated_frame, label, (x1, y1 - 10), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
            for track_id in list(self.tracked_persons.keys()):
                if track_id not in current_track_ids and self.tracked_persons[track_id]['in_frame']:
                    if self.tracked_persons[track_id]['recognized']:
                        self.update_presence_time(track_id, current_time, False)
                    self.tracked_persons[track_id]['in_frame'] = False
            
        except Exception as e:
            logger.error(f"Error processing frame: {e}")
        
        person_count = len(current_track_ids)
        recognized_count = len([p for p in self.tracked_persons.values() if p['recognized']])
        in_frame_count = len([p for p in self.tracked_persons.values() if p['in_frame']])
        
        stats_bg = annotated_frame[10:200, 10:400].copy()
        annotated_frame[10:200, 10:400] = cv2.addWeighted(stats_bg, 0.3, np.zeros_like(stats_bg), 0.7, 0)
        
        cv2.putText(annotated_frame, f"Persons in Frame: {person_count}", (20, 40), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(annotated_frame, f"Recognized: {recognized_count}/{len(self.all_required_persons)}", (20, 70), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(annotated_frame, f"Currently Present: {in_frame_count}", (20, 100), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        current_time_str = current_time.strftime('%H:%M:%S')
        cv2.putText(annotated_frame, f"Time: {current_time_str}", (20, 130), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        return annotated_frame, person_count, recognized_count, in_frame_count
    
    def generate_attendance_report(self):
        current_time = datetime.now()
        records_list = []
        
        recognized_persons = set()
        
        for track_id, data in self.tracked_persons.items():
            if data['recognized'] and data['name'] != "Unknown":
                recognized_persons.add(data['name'])
                
                final_duration = data['total_duration']
                if data['current_session_start'] is not None:
                    current_session_duration = (current_time - data['current_session_start']).total_seconds()
                    final_duration += current_session_duration
                
                if data['in_frame']:
                    end_time = current_time
                    status = 'Still Present'
                else:
                    end_time = data['last_seen_time']
                    status = 'Left'
                
                total_duration = data['total_duration']
                if data['current_session_start'] is not None:
                    current_session_duration = (current_time - data['current_session_start']).total_seconds()
                    total_duration += current_session_duration
                
                hours = int(total_duration // 3600)
                minutes = int((total_duration % 3600) // 60)
                seconds = int(total_duration % 60)
                duration_formatted = f"{hours}h {minutes}m {seconds}s"
                
                record = {
                    'Name': data['name'],
                    'Date': data['first_seen_time'].date().isoformat(),
                    'Start_Time': data['first_seen_time'].strftime('%H:%M:%S'),
                    'End_Time': end_time.strftime('%H:%M:%S'),
                    'Total_Duration_Seconds': total_duration,
                    'Total_Duration_Formatted': duration_formatted,
                    'Status': status,
                    'Sessions_Present': list(data.get('sessions_present', []))
                }
                records_list.append(record)
        
        for person in self.all_required_persons:
            if person not in recognized_persons:
                record = {
                    'Name': person,
                    'Date': current_time.date().isoformat(),
                    'Start_Time': 'Never',
                    'End_Time': 'Never',
                    'Total_Duration_Seconds': 0,
                    'Total_Duration_Formatted': '0s',
                    'Status': 'Absent',
                    'Sessions_Present': []
                }
                records_list.append(record)
        
        records_list.sort(key=lambda x: x['Name'])
        return records_list
    
    async def start_camera(self, camera_source):
        if self.cap is not None:
            self.cap.release()
            
        self.current_camera_source = camera_source
        
        try:
            source = int(camera_source)
        except ValueError:
            source = camera_source
            
        self.cap = cv2.VideoCapture(source)
        
        if isinstance(source, int):
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self.is_running = True
        
        if not self.cap.isOpened():
            raise Exception(f"Could not open video source: {camera_source}")
        
        logger.info(f"[CAMERA] Successfully started camera: {camera_source}")
        return True

    def stop_camera(self):
        self.is_running = False
        if self.cap is not None:
            self.cap.release()
            self.cap = None
            logger.info("[CAMERA] Camera stopped")

    def set_camera_zoom(self, zoom_factor: float):
        self.camera_zoom = max(0.1, min(3.0, zoom_factor))
        logger.info(f"[CAMERA] Zoom set to: {self.camera_zoom}x")

class MeetingManager:
    def __init__(self):
        self.current_meeting = None
        self.audio_recorder = SimpleAudioRecorder()
        self.summarizer = BalancedMeetingSummarizer()
        self.diarizer = EnhancedAudioDiarizer()
        self.meeting_start_time = None
        self.audio_diarization_completed = False
        self.transcript_data = None
        self.stored_emails = []
        
        self.audio_start_time = None
        self.audio_stop_time = None

    def create_meeting(self, title: str, agenda: str = "", emails: str = None) -> Dict:
        import uuid
        
        clean_title = clean_meeting_title_for_display(title)
        
        current_date = datetime.now().strftime('%Y-%m-%d')
        
        meeting_id = f"{clean_title.replace(' ', '_').lower()}"
        
        folder_name = f"{clean_title.replace(' ', '_')}_{current_date}"
        
        meeting_folder = os.path.join(MEETINGS_DATA_DIR, folder_name)
        os.makedirs(meeting_folder, exist_ok=True)
        
        self.meeting_start_time = datetime.now()
        
        self.stored_emails = []
        if emails:
            self.stored_emails = [e.strip() for e in emails.split(',') if e.strip()]
        
        self.current_meeting = {
            'id': meeting_id,
            'title': clean_title,
            'original_title': title,
            'agenda': agenda,
            'emails': self.stored_emails,
            'folder': meeting_folder,
            'folder_name': folder_name,
            'start_time': self.meeting_start_time.isoformat(),
            'date': current_date,
            'status': 'active',
            'attendance_data': [],
            'generated_files': {},
            'audio_file': os.path.join(meeting_folder, f"{folder_name}_audio.wav"),
            'summary_file': os.path.join(meeting_folder, f"{folder_name}_summary.pdf"),
            'transcript_file': os.path.join(meeting_folder, f"{folder_name}_transcript.json"),
            'transcript_pdf_file': os.path.join(meeting_folder, f"{folder_name}_transcript.pdf"),
            'attendance_file': os.path.join(meeting_folder, f"{folder_name}_attendance.xlsx"),
            'recording_active': False,
            'recording_start_time': None,
            'attendance_active': False,
            'video_recording_active': False,
        }
        
        meeting_file = os.path.join(meeting_folder, f"{folder_name}_meeting_data.json")
        with open(meeting_file, 'w', encoding='utf-8') as f:
            json.dump(self.current_meeting, f, indent=2, default=str, ensure_ascii=False)
        
        logger.info(f"📅 Meeting created: '{clean_title}'")
        logger.info(f"📁 Folder: {folder_name}")
        logger.info(f"📅 Date: {current_date}")
        logger.info(f"📧 Stored emails: {self.stored_emails}")
        
        return self.current_meeting

    def format_duration(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def create_transcript_pdf(self, transcript_data):
        try:
            if not self.current_meeting:
                logger.warning("⚠️ No active meeting for transcript PDF")
                return None
            
            meeting_folder = self.current_meeting['folder']
            folder_name = os.path.basename(meeting_folder)
            pdf_path = os.path.join(meeting_folder, f"{folder_name}_transcript.pdf")
            
            meeting_title = self.current_meeting.get('title', 'Meeting Discussion')
            
            return create_enhanced_transcript_pdf(transcript_data, meeting_title, pdf_path)
            
        except Exception as e:
            logger.error(f"❌ Error creating transcript PDF: {e}")
            traceback.print_exc()
            return None

    def get_stored_emails(self):
        return self.stored_emails
    
    def start_attendance_tracking(self):
        if self.current_meeting:
            global meeting_status
            meeting_status['attendance_active'] = True
            
            attendance_system.start_attendance_session()
            
            self.current_meeting['attendance_started'] = datetime.now().isoformat()
            logger.info("✅ Attendance tracking started")
            return True
        return False

    def start_audio_recording(self):
        if self.current_meeting:
            try:
                meeting_id = self.current_meeting['id']
                meeting_folder = self.current_meeting['folder']
                success = self.audio_recorder.start_recording(meeting_id, meeting_folder)
                if success:
                    global meeting_status
                    meeting_status['audio_recording_active'] = True
                    
                    self.audio_start_time = datetime.now()
                    self.current_meeting['audio_started'] = self.audio_start_time.isoformat()
                    
                    logger.info(f"✅ Audio recording started at {self.audio_start_time}")
                    return True
            except Exception as e:
                logger.error(f"❌ Failed to start audio recording: {e}")
        return False
    
    def stop_audio_recording(self):
        global meeting_status
        meeting_status['audio_recording_active'] = False
        
        self.audio_stop_time = datetime.now()
        
        audio_file = self.audio_recorder.stop_recording()
        
        if self.current_meeting:
            self.current_meeting['audio_stopped'] = self.audio_stop_time.isoformat()
            if audio_file:
                self.current_meeting['audio_file'] = audio_file
        
        logger.info(f"🛑 Audio recording stopped at {self.audio_stop_time}")
        return True
    
    def stop_attendance_tracking(self):
        global meeting_status
        meeting_status['attendance_active'] = False
        
        if self.current_meeting:
            self.current_meeting['attendance_stopped'] = datetime.now().isoformat()
        
        logger.info("🛑 Attendance tracking stopped")
        return True

    def export_attendance_excel(self):
        try:
            if not self.current_meeting:
                return None
            
            meeting_folder = self.current_meeting['folder']
            folder_name = os.path.basename(meeting_folder)
            excel_path = os.path.join(meeting_folder, f"{folder_name}_attendance.xlsx")
            
            records_list = attendance_system.generate_attendance_report()
            
            if records_list:
                workbook = xlsxwriter.Workbook(excel_path)
                worksheet = workbook.add_worksheet('Attendance')
                
                header_format = workbook.add_format({
                    'bold': True,
                    'bg_color': '#0D47A1',
                    'font_color': 'white',
                    'border': 1,
                    'align': 'center',
                    'valign': 'vcenter'
                })
                
                headers = ['Name', 'Session 1', 'Session 2', 'Overall Status']
                
                for col, header in enumerate(headers):
                    worksheet.write(0, col, header, header_format)
                
                person_records = {}
                for record in records_list:
                    name = record['Name']
                    if name not in person_records:
                        person_records[name] = {
                            'sessions': [record.get('Status', 'Absent')],
                            'times': {
                                'first_seen': record.get('Start_Time', 'Never'),
                                'last_seen': record.get('End_Time', 'Never')
                            }
                        }
                    else:
                        person_records[name]['sessions'].append(record.get('Status', 'Absent'))
                
                row = 1
                for name, data in person_records.items():
                    sessions = data['sessions']
                    
                    session1_status = sessions[0] if len(sessions) > 0 else 'Absent'
                    session2_status = sessions[1] if len(sessions) > 1 else 'Absent'
                    
                    if 'Present' in sessions or 'Still Present' in sessions:
                        overall_status = 'Present'
                    elif 'Left' in sessions:
                        overall_status = 'Left'
                    else:
                        overall_status = 'Absent'
                    
                    worksheet.write(row, 0, name)
                    
                    session1_format = workbook.add_format()
                    if session1_status in ['Present', 'Still Present']:
                        session1_format.set_bg_color('#C6EFCE')
                        session1_text = 'Present'
                    elif session1_status == 'Left':
                        session1_format.set_bg_color('#FFEB3B')
                        session1_text = 'Left'
                    else:
                        session1_format.set_bg_color('#FFC7CE')
                        session1_text = 'Absent'
                    worksheet.write(row, 1, session1_text, session1_format)
                    
                    session2_format = workbook.add_format()
                    if session2_status in ['Present', 'Still Present']:
                        session2_format.set_bg_color('#C6EFCE')
                        session2_text = 'Present'
                    elif session2_status == 'Left':
                        session2_format.set_bg_color('#FFEB3B')
                        session2_text = 'Left'
                    else:
                        session2_format.set_bg_color('#FFC7CE')
                        session2_text = 'Absent'
                    worksheet.write(row, 2, session2_text, session2_format)
                    
                    overall_format = workbook.add_format({'bold': True})
                    if overall_status == 'Present':
                        overall_format.set_bg_color('#C6EFCE')
                    elif overall_status == 'Left':
                        overall_format.set_bg_color('#FFEB3B')
                    else:
                        overall_format.set_bg_color('#FFC7CE')
                    worksheet.write(row, 3, overall_status, overall_format)
                    
                    row += 1
                
                worksheet.set_column(0, 0, 30)
                worksheet.set_column(1, 3, 15)
                
                workbook.close()
                logger.info(f"✅ Excel report saved: {excel_path}")
                
                self.current_meeting['attendance_file'] = excel_path
                self.current_meeting['generated_files']['attendance_excel'] = os.path.basename(excel_path)
                
                return excel_path
            
            return None
            
        except Exception as e:
            logger.error(f"Error exporting attendance Excel: {e}")
            return None

    def process_audio_diarization(self):
        if not self.current_meeting or not self.current_meeting.get('audio_file'):
            logger.warning("⚠️ No audio file to process")
            return None
        
        audio_file = self.current_meeting['audio_file']
        if not os.path.exists(audio_file):
            logger.error(f"❌ Audio file not found: {audio_file}")
            return None
        
        try:
            logger.info("🎙️ Starting enhanced audio diarization...")
            
            agenda = self.current_meeting.get('agenda', '')
            meeting_title = self.current_meeting.get('title', '')
            
            transcript_data = self.diarizer.diarize_audio(audio_file, agenda, use_manual_fallback=False, meeting_title=meeting_title)
            
            if transcript_data:
                meeting_folder = self.current_meeting['folder']
                folder_name = os.path.basename(meeting_folder)
                
                transcript_file = os.path.join(meeting_folder, f"{folder_name}_transcript.json")
                with open(transcript_file, 'w', encoding='utf-8') as f:
                    json.dump(transcript_data, f, indent=4, ensure_ascii=False)
                
                self.transcript_data = transcript_data
                self.audio_diarization_completed = True
                self.current_meeting['transcript_file'] = transcript_file
                
                logger.info("📄 Creating transcript PDF...")
                transcript_pdf = self.create_transcript_pdf(transcript_data)
                if transcript_pdf:
                    self.current_meeting['transcript_pdf_file'] = transcript_pdf
                
                logger.info(f"✅ Enhanced audio diarization completed")
                logger.info(f"📄 Meeting transcript JSON: {transcript_file}")
                if transcript_pdf:
                    logger.info(f"📄 Meeting transcript PDF: {transcript_pdf}")
                
                if 'transcript' in transcript_data:
                    segments = transcript_data['transcript']
                    logger.info(f"📊 Transcript summary:")
                    logger.info(f"   Total segments: {len(segments)}")
                    logger.info(f"   Total duration: {transcript_data.get('total_duration', 0):.1f}s")
                    speakers = set(segment['speaker'] for segment in segments)
                    logger.info(f"   Unique speakers: {len(speakers)}")
                    logger.info(f"   Speakers: {', '.join(speakers)}")
                
                return transcript_data
            else:
                logger.error("❌ Enhanced audio diarization failed")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error in enhanced audio diarization: {e}")
            traceback.print_exc()
            return None

    def generate_meeting_summary(self):
        if not self.current_meeting:
            logger.warning("⚠️ No active meeting for summary generation")
            return None
        
        if not self.transcript_data and self.current_meeting.get('transcript_file'):
            transcript_file = self.current_meeting['transcript_file']
            if os.path.exists(transcript_file):
                with open(transcript_file, 'r', encoding='utf-8') as f:
                    self.transcript_data = json.load(f)
        
        if not self.transcript_data:
            logger.warning("⚠️ No transcript data available for summary")
            return None
        
        try:
            logger.info("📝 Generating balanced meeting summary...")
            
            meeting_title = self.current_meeting.get('title', 'Meeting Discussion')
            agenda = self.current_meeting.get('agenda', '')
            
            analysis, meeting_id, agenda_analysis = self.summarizer.generate_balanced_analysis(
                self.transcript_data, 
                meeting_title, 
                agenda
            )
            
            if analysis:
                pdf_path = self.summarizer.create_balanced_pdf(
                    analysis,
                    meeting_id,
                    self.transcript_data,
                    agenda_analysis,
                    meeting_title,
                    agenda,
                    self.current_meeting['summary_file']
                )
                
                if pdf_path:
                    self.current_meeting['summary_generated'] = datetime.now().isoformat()
                    self.current_meeting['summary_path'] = pdf_path
                    self.current_meeting['summary_meeting_id'] = meeting_id
                    
                    logger.info(f"✅ Balanced meeting summary generated: {pdf_path}")
                    logger.info(f"📊 Agenda relevance: {agenda_analysis['relevance_percentage']:.1f}%")
                    
                    return pdf_path
            
            logger.warning("⚠️ Summary generation failed or returned empty")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error generating balanced meeting summary: {e}")
            traceback.print_exc()
            return None

    def cleanup_recording_resources(self):
        try:
            if meeting_status['audio_recording_active']:
                self.stop_audio_recording()
            
            meeting_status['audio_recording_active'] = False
            
        except Exception as e:
            logger.error(f"❌ Error cleaning up recording resources: {e}")

    def end_meeting(self):
        try:
            logger.info(f"🏁 Ending meeting: {self.current_meeting['id']}")
            
            self.cleanup_recording_resources()
            
            if meeting_status['attendance_active']:
                self.stop_attendance_tracking()
            
            logger.info("🎙️ Processing audio diarization...")
            transcript_data = self.process_audio_diarization()
            
            transcript_pdf = None
            if transcript_data:
                logger.info("📄 Creating transcript PDF...")
                transcript_pdf = self.create_transcript_pdf(transcript_data)
            
            logger.info("📊 Generating attendance report...")
            attendance_file = self.export_attendance_excel()
            
            logger.info("📝 Generating meeting summary...")
            summary_file = self.generate_meeting_summary()
            
            end_time = datetime.now()
            start_time = self.current_meeting.get('start_time', end_time)
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            duration = (end_time - start_time).total_seconds()
            self.current_meeting['duration'] = self.format_duration(duration)
            
            self.current_meeting['end_time'] = end_time.isoformat()
            self.current_meeting['status'] = 'ended'
            
            generated_files = {}
            if transcript_data:
                generated_files['transcript'] = self.current_meeting.get('transcript_file')
            if transcript_pdf:
                generated_files['transcript_pdf'] = transcript_pdf
            if attendance_file:
                generated_files['attendance'] = attendance_file
            if summary_file:
                generated_files['summary'] = summary_file
            
            self.current_meeting['generated_files'] = generated_files
            
            logger.info(f"✅ Meeting ended successfully: {self.current_meeting['id']}")
            
            return self.current_meeting
            
        except Exception as e:
            logger.error(f"❌ Error ending meeting: {e}", exc_info=True)
            return None

    def get_meeting_status(self) -> Dict:
        if not self.current_meeting:
            return {'active': False}
        
        status = {
            'active': True,
            'meeting': self.current_meeting,
            'attendance_active': meeting_status['attendance_active'],
            'audio_recording_active': meeting_status['audio_recording_active'],
            'audio_diarization_completed': self.audio_diarization_completed,
            'transcript_available': self.transcript_data is not None,
            'stored_emails': self.stored_emails
        }
        
        return status

attendance_system = AdvancedAttendanceSystem()
meeting_manager = MeetingManager()
email_automator = EmailAutomator()

def log_error_with_context(e, context):
    error_id = str(uuid.uuid4())
    logger.error(f"❌ Error ID: {error_id}")
    logger.error(f"❌ Error: {e}")
    logger.error(f"❌ Context: {context}")
    traceback.print_exc()
    return error_id

@app.on_event("startup")
async def startup_event():
    try:
        # Verify environment variables are loaded
        logger.info(f"📋 Environment loaded: LLM_API_KEY configured: {bool(config.LLM_API_KEY)}")
        logger.info(f"📋 Email sender: {config.EMAIL_SENDER}")
        
        attendance_system.initialize_models()
        attendance_system.load_known_faces()
        
        logger.info("[SYSTEM] Initializing diarization models...")
        meeting_manager.diarizer._initialize_models()
        
        logger.info("[SYSTEM] ✅ Complete meeting system initialized successfully")
    except Exception as e:
        logger.error(f"[SYSTEM] ❌ Failed to initialize: {e}")
        # Don't raise, let the server start but log the error

@app.get("/")
async def get_html():
    html_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    else:
        return HTMLResponse(content="<h1>MeetingSense System</h1><p>System is running. Frontend not found.</p>")

# ============================================================================
# UPDATED WEBSOCKET ENDPOINTS (REQUESTED CHANGES)
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for attendance camera feed ONLY - not for OBS Virtual Camera"""
    await websocket.accept()
    active_connections.append(websocket)
    attendance_system.ws = websocket
    
    try:
        while True:
            try:
                # Check for ping messages
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                    message = json.loads(data)
                    if message.get('type') == 'ping':
                        await websocket.send_text(json.dumps({'type': 'pong'}))
                        continue
                except (asyncio.TimeoutError, json.JSONDecodeError):
                    pass
                except:
                    break
                
                # ONLY handle attendance camera - NEVER switch to OBS Virtual Camera
                if attendance_system.is_running and attendance_system.cap is not None:
                    try:
                        ret, frame = attendance_system.cap.read()
                        if not ret:
                            await asyncio.sleep(0.1)
                            continue
                        
                        # Process for attendance tracking only
                        annotated_frame, person_count, recognized_count, in_frame_count = attendance_system.process_frame_with_tracking(frame)
                        
                        # Encode and send frame
                        _, buffer = cv2.imencode('.jpg', annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                        frame_data = base64.b64encode(buffer).decode('utf-8')
                        
                        message = {
                            "type": "frame",
                            "frame": frame_data,
                            "person_count": person_count,
                            "recognized_count": recognized_count,
                            "in_frame_count": in_frame_count,
                            "timestamp": datetime.now().isoformat(),
                            "message": "Attendance Camera"
                        }
                        
                        await websocket.send_text(json.dumps(message))
                        await asyncio.sleep(0.05)  # ~20 FPS
                        
                    except Exception as e:
                        logger.error(f"[WEBSOCKET] Attendance error: {e}")
                        await asyncio.sleep(0.1)
                else:
                    # No camera active - wait
                    await asyncio.sleep(1.0)
                    
            except WebSocketDisconnect:
                logger.info("[WEBSOCKET] Client disconnected")
                break
            except Exception as e:
                logger.error(f"[WEBSOCKET] Error: {e}")
                break
                
    except Exception as e:
        logger.error(f"[WEBSOCKET] Connection error: {e}")
    finally:
        if websocket in active_connections:
            active_connections.remove(websocket)
        attendance_system.ws = None
        logger.info("[WEBSOCKET] Connection closed")

@app.websocket("/ws_live_feed")
async def live_feed_websocket(websocket: WebSocket):
    """WebSocket for OBS Virtual Camera live feed ONLY - for recording display"""
    await websocket.accept()
    
    # IMPORTANT: Wait for OBS to be ready
    await asyncio.sleep(2)
    
    cap = None
    last_error_time = 0
    
    try:
        while True:
            try:
                # Check if recording is active
                if not meeting_status['video_recording_active']:
                    logger.debug("[LIVE_FEED] Recording not active, waiting...")
                    await asyncio.sleep(1.0)
                    continue
                
                # Try to open OBS Virtual Camera
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                        cap = None
                    
                    # FIX: Get OBS Virtual Camera index
                    camera_index = CameraDetector.get_obs_camera_index()
                    logger.info(f"[LIVE_FEED] Trying OBS Virtual Camera at index {camera_index}")
                    
                    cap = cv2.VideoCapture(camera_index)
                    
                    if cap.isOpened():
                        # Set properties for OBS Virtual Camera
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                        cap.set(cv2.CAP_PROP_FPS, 30)
                        logger.info(f"[LIVE_FEED] Connected to OBS Virtual Camera at index {camera_index}")
                    else:
                        logger.warning(f"[LIVE_FEED] OBS Virtual Camera not ready at index {camera_index}")
                        cap = None
                        await asyncio.sleep(2.0)
                        continue
                
                # Read frame from OBS Virtual Camera
                ret, frame = cap.read()
                if not ret:
                    logger.warning("[LIVE_FEED] Failed to read frame from OBS Virtual Camera")
                    cap.release()
                    cap = None
                    await asyncio.sleep(1.0)
                    continue
                
                # Add "REC" indicator to show it's recording
                cv2.putText(frame, "● REC", (10, 30), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                
                # Add timestamp
                timestamp = datetime.now().strftime("%H:%M:%S")
                cv2.putText(frame, timestamp, (frame.shape[1] - 120, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                # Resize for efficiency
                frame = cv2.resize(frame, (640, 480))
                
                # Encode and send frame
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                frame_data = base64.b64encode(buffer).decode('utf-8')
                
                message = {
                    "type": "frame",
                    "frame": frame_data,
                    "timestamp": datetime.now().isoformat(),
                    "message": "OBS Recording - LIVE"
                }
                
                await websocket.send_text(json.dumps(message))
                await asyncio.sleep(0.033)  # ~30 FPS
                
            except WebSocketDisconnect:
                logger.info("[LIVE_FEED] Client disconnected")
                break
            except Exception as e:
                current_time = time.time()
                if current_time - last_error_time > 5:  # Log errors max every 5 seconds
                    logger.error(f"[LIVE_FEED] Error: {e}")
                    last_error_time = current_time
                if cap is not None:
                    cap.release()
                    cap = None
                await asyncio.sleep(1.0)
                
    except Exception as e:
        logger.error(f"[LIVE_FEED] Connection error: {e}")
    finally:
        if cap is not None:
            cap.release()
        logger.info("[LIVE_FEED] Connection closed")

def generate_frames():
    while True:
        if not attendance_system.is_running or attendance_system.cap is None:
            time.sleep(0.1)
            continue
            
        ret, frame = attendance_system.cap.read()
        if not ret:
            time.sleep(0.1)
            continue
            
        with frame_lock:
            global_frame = frame.copy()
        
        try:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ret:
                time.sleep(0.05)
                continue
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        except Exception:
            time.sleep(0.05)
            continue

@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.post("/create_meeting")
async def create_meeting_safe(
    title: str = Form(...),
    agenda: str = Form(""),
    emails: str = Form(None)
):
    try:
        logger.info(f"📝 Creating meeting: {title}")
        
        created_meeting = meeting_manager.create_meeting(
            title=title, 
            agenda=agenda,
            emails=emails
        )
        
        logger.info(f"✅ Meeting created successfully: {created_meeting['id']}")
        
        return {
            "status": "success", 
            "message": f"Meeting '{title}' created successfully",
            "meeting": created_meeting
        }
        
    except Exception as e:
        error_id = log_error_with_context(e, {
            "endpoint": "create_meeting",
            "title": title,
            "agenda": agenda,
            "emails": emails
        })
        return {
            "status": "error", 
            "message": f"Failed to create meeting: {str(e)}",
            "error_id": error_id
        }

@app.post("/start_attendance")
async def start_attendance():
    global meeting_status
    
    try:
        logger.info("🎯 Starting attendance tracking")
        
        # FIX 2: Ensure camera is started
        camera_source = "0"  # Default webcam index
        
        # Try to start camera first
        try:
            await attendance_system.start_camera(camera_source)
            logger.info(f"✅ Camera started on source: {camera_source}")
        except Exception as cam_error:
            logger.error(f"❌ Failed to start camera: {cam_error}")
            return {
                "status": "error",
                "message": f"Failed to start camera: {str(cam_error)}"
            }
        
        # Start attendance session
        attendance_system.start_attendance_session()
        meeting_status['attendance_active'] = True
        
        if meeting_manager.current_meeting:
            meeting_manager.current_meeting['attendance_started'] = datetime.now().isoformat()
        
        logger.info("✅ Attendance tracking started")
        
        return {
            "status": "success",
            "message": "Attendance tracking started",
            "camera_source": camera_source
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to start attendance: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/stop_attendance")
async def stop_attendance():
    try:
        logger.info("🛑 Stopping attendance tracking")
        success = meeting_manager.stop_attendance_tracking()
        if success:
            logger.info("✅ Attendance tracking stopped")
            return {
                "status": "success",
                "message": "Attendance tracking stopped"
            }
        else:
            logger.warning("⚠️ No active attendance tracking found")
            return {
                "status": "error",
                "message": "No active attendance tracking found"
            }
    except Exception as e:
        logger.error(f"❌ Failed to stop attendance: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/start_audio_recording")
async def start_audio_recording():
    try:
        logger.info("🎙️ Starting audio recording")
        success = meeting_manager.start_audio_recording()
        if success:
            logger.info("✅ Audio recording started")
            return {
                "status": "success",
                "message": "Audio recording started"
            }
        else:
            logger.warning("⚠️ No active meeting found for audio recording")
            return {
                "status": "error",
                "message": "No active meeting found or failed to start recording"
            }
    except Exception as e:
        logger.error(f"❌ Failed to start audio recording: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/stop_audio_recording")
async def stop_audio_recording():
    try:
        logger.info("🛑 Stopping audio recording")
        success = meeting_manager.stop_audio_recording()
        if success:
            logger.info("✅ Audio recording stopped")
            return {
                "status": "success",
                "message": "Audio recording stopped"
            }
        else:
            logger.warning("⚠️ No active audio recording found")
            return {
                "status": "error",
                "message": "No active audio recording found"
            }
    except Exception as e:
        logger.error(f"❌ Failed to stop audio recording: {e}")
        return {"status": "error", "message": str(e)}

# ============================================================================
# UPDATED VIDEO RECORDING ENDPOINTS (REQUESTED CHANGES)
# ============================================================================

@app.post("/start_video_recording")
async def start_video_recording():
    """Start video recording via OBS - without affecting attendance camera"""
    global meeting_status, obs_controller
    
    try:
        if not meeting_manager.current_meeting:
            return {
                "status": "error", 
                "message": "No active meeting. Create a meeting first."
            }
        
        # 1. Connect to OBS
        if obs_controller.state == RecorderState.DISCONNECTED:
            logger.info("🔗 Connecting to OBS...")
            if not obs_controller.connect():
                return {
                    "status": "error", 
                    "message": "Failed to connect to OBS."
                }
        
        # 2. IMPORTANT: Ensure virtual camera is ready BEFORE recording
        logger.info("🎥 Preparing OBS Virtual Camera...")
        obs_controller.ensure_virtual_camera_active()
        
        # 3. Set recording target
        meeting_folder = meeting_manager.current_meeting['folder']
        folder_name = os.path.basename(meeting_folder)
        filename = f"{folder_name}_video"
        
        success = obs_controller.set_recording_target(meeting_folder, filename)
        if not success:
            return {
                "status": "error", 
                "message": "Failed to set recording target"
            }
        
        # 4. Start recording
        success, message = obs_controller.start_recording()
        
        if success:
            meeting_status['video_recording_active'] = True
            if meeting_manager.current_meeting:
                meeting_manager.current_meeting['video_started'] = datetime.now().isoformat()
                meeting_manager.current_meeting['video_recording_active'] = True
                meeting_manager.current_meeting['video_filename'] = f"{folder_name}_video.mp4"
            
            logger.info(f"✅ Video recording started with OBS Virtual Camera")
            
            return {
                "status": "success", 
                "message": "Video recording started with OBS Virtual Camera",
                "live_feed_url": "/ws_live_feed",
                "virtual_camera_ready": True
            }
        else:
            return {
                "status": "error", 
                "message": f"Failed to start recording: {message}"
            }
            
    except Exception as e:
        logger.error(f"❌ Error starting video recording: {e}")
        return {
            "status": "error", 
            "message": f"Unexpected error: {str(e)[:200]}"
        }

@app.post("/stop_video_recording")
async def stop_video_recording():
    """Stop video recording with integrity guarantees"""
    global meeting_status, obs_controller
    
    try:
        if not meeting_status['video_recording_active']:
            return {
                "status": "error", 
                "message": "No active video recording"
            }
        
        logger.info("🛑 Stopping video recording with integrity checks...")
        
        # Use the enhanced stop_recording method
        success, result = obs_controller.stop_recording()
        
        if success:
            meeting_status['video_recording_active'] = False
            
            if meeting_manager.current_meeting:
                meeting_manager.current_meeting['video_stopped'] = datetime.now().isoformat()
                meeting_manager.current_meeting['video_recording_active'] = False
                
                # Store video file path
                if isinstance(result, str) and os.path.exists(result):
                    meeting_manager.current_meeting['video_file'] = result
                    meeting_manager.current_meeting['generated_files']['video'] = os.path.basename(result)
                elif obs_controller.last_recording_path:
                    meeting_manager.current_meeting['video_file'] = obs_controller.last_recording_path
                    meeting_manager.current_meeting['generated_files']['video'] = os.path.basename(obs_controller.last_recording_path)
            
            logger.info("✅ Video recording stopped safely")
            
            return {
                "status": "success", 
                "message": "Video recording stopped and verified",
                "video_path": result if isinstance(result, str) else None,
                "integrity": "verified"
            }
        else:
            return {
                "status": "error", 
                "message": f"Failed to stop recording: {result}"
            }
            
    except Exception as e:
        logger.error(f"❌ Error stopping video recording: {e}")
        traceback.print_exc()
        return {
            "status": "error", 
            "message": f"Unexpected error: {str(e)[:200]}"
        }

@app.get("/video_recording_status")
async def video_recording_status():
    """Check video recording status"""
    return {
        "video_recording_active": meeting_status.get('video_recording_active', False),
        "obs_state": obs_controller.state.name if obs_controller else "DISCONNECTED"
    }

@app.get("/obs_setup_instructions")
async def obs_setup_instructions():
    """Get OBS setup instructions"""
    instructions = """
    🎥 OBS STUDIO SETUP INSTRUCTIONS:
    
    1. Open OBS Studio
    2. Create a Scene (e.g., 'MeetingScene')
    3. Add these sources:
       - Video Capture Device → Select your camera
       - Audio Input Capture → Select your microphone
    4. Configure WebSocket:
       - Tools → WebSocket Server Settings
       - Enable WebSocket server
       - Port: 4455
       - No password
       - Click OK
    5. Close OBS Studio
    
    6. Now start your meeting in the app
    7. Click 'Start Video Recording'
    
    The app will:
    - Connect to OBS
    - Start Virtual Camera (for live feed)
    - Start recording from your pre-configured scene
    
    Virtual Camera shows the OBS output for live feed.
    Recording saves the actual OBS scene with your camera/mic.
    """
    
    return HTMLResponse(f"<pre>{instructions}</pre>")

# ============================================================================
# DIAGNOSTIC ENDPOINT FOR DEBUGGING
# ============================================================================

@app.get("/obs_diagnostics")
async def obs_diagnostics():
    """Get detailed OBS diagnostics"""
    try:
        diagnostics = {
            "timestamp": datetime.now().isoformat(),
            "obs_connected": obs_controller.client is not None,
            "obs_state": obs_controller.state,
            "video_recording_active": meeting_status['video_recording_active'],
            "last_recording_path": obs_controller.last_recording_path,
            "integrity_checks_enabled": True,
            "system_status": {
                "disk_space": {},
                "obs_process": obs_controller._is_obs_running(),
                "websocket_port_open": obs_controller._check_port_open(4455)
            }
        }
        
        # Check disk space for common OBS locations
        common_paths = [
            os.path.expanduser("~"),
            os.path.expanduser("~/Videos"),
            os.path.expanduser("~/Documents"),
        ]
        
        if obs_controller.client:
            try:
                record_dir = obs_controller.client.get_record_directory().record_directory
                common_paths.insert(0, record_dir)
                diagnostics['obs_record_directory'] = record_dir
            except:
                pass
        
        for path in common_paths:
            if os.path.exists(path):
                try:
                    usage = shutil.disk_usage(path)
                    diagnostics['system_status']['disk_space'][path] = {
                        "total_gb": usage.total / (1024**3),
                        "free_gb": usage.free / (1024**3),
                        "used_percent": (usage.used / usage.total) * 100
                    }
                except:
                    pass
        
        # Get OBS status if connected
        if obs_controller.client:
            try:
                status_text = obs_controller.get_status()
                diagnostics['obs_status_text'] = status_text
                
                version = obs_controller.client.get_version()
                diagnostics['obs_version'] = version.obs_version
                
                record_status = obs_controller.client.get_record_status()
                diagnostics['record_status'] = {
                    "active": record_status.output_active,
                    "paused": record_status.output_paused if hasattr(record_status, 'output_paused') else False
                }
                
                vcam_status = obs_controller.client.get_virtual_cam_status()
                diagnostics['virtual_cam_status'] = {
                    "active": vcam_status.output_active
                }
            except Exception as e:
                diagnostics['obs_status_error'] = str(e)
        
        return JSONResponse({
            "status": "success",
            "diagnostics": diagnostics
        })
        
    except Exception as e:
        logger.error(f"❌ Diagnostics error: {e}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        })

@app.post("/end_meeting")
async def end_meeting_api():
    try:
        logger.info("🏁 API: Ending current meeting")
        
        # Stop video recording if active
        if meeting_status['video_recording_active']:
            logger.info("🛑 Stopping video recording...")
            await stop_video_recording()
        
        meeting_result = meeting_manager.end_meeting()
        
        if meeting_result:
            logger.info(f"✅ Meeting ended: {meeting_result['id']}")
            
            folder_name = os.path.basename(meeting_result.get('folder', ''))
            
            return {
                "status": "success",
                "message": "Meeting ended successfully",
                "meeting": {
                    "id": meeting_result.get('id'),
                    "title": meeting_result.get('title', 'Meeting'),
                    "date": meeting_result.get('date', datetime.now().strftime('%Y-%m-%d')),
                    "duration": meeting_result.get('duration', '00:00:00'),
                    "folder": folder_name,
                    "files": meeting_result.get('generated_files', {})
                }
            }
        else:
            logger.error("❌ Failed to end meeting")
            return {
                "status": "error",
                "message": "Failed to end meeting"
            }
            
    except Exception as e:
        logger.error(f"❌ API Error ending meeting: {e}")
        traceback.print_exc()
        return {
            "status": "error",
            "message": f"Server error: {str(e)[:100]}"
        }

@app.get("/meeting_status")
async def get_meeting_status():
    try:
        status = meeting_manager.get_meeting_status()
        logger.debug("📊 Meeting status checked")
        return {
            "status": "success",
            "data": status
        }
    except Exception as e:
        logger.error(f"❌ Failed to get meeting status: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/start_camera")
async def start_camera(camera_source: str = Form(...)):
    try:
        logger.info(f"📷 Starting camera: {camera_source}")
        await attendance_system.start_camera(camera_source)
        
        logger.info(f"✅ Camera started: {camera_source}")
        return {"status": "success", "message": f"Camera started with source: {camera_source}"}
    except Exception as e:
        logger.error(f"❌ Failed to start camera: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/stop_camera")
async def stop_camera():
    logger.info("📷 Stopping camera and cleaning up...")
    
    try:
        attendance_system.stop_camera()
        
        for ws in active_connections.copy():
            try:
                await ws.close()
            except:
                pass
        active_connections.clear()
        
        logger.info("✅ Camera stopped and connections cleaned up")
        return {"status": "success", "message": "Camera stopped successfully"}
    except Exception as e:
        logger.error(f"❌ Error stopping camera: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/set_camera_zoom")
async def set_camera_zoom(zoom_factor: float = Form(...)):
    try:
        attendance_system.set_camera_zoom(zoom_factor)
        return {
            "status": "success",
            "message": f"Camera zoom set to {zoom_factor}x"
        }
    except Exception as e:
        logger.error(f"❌ Failed to set camera zoom: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/add_attendee")
async def add_attendee(
    name: str = Form(...),
    photo: UploadFile = File(None),
    audio: UploadFile = File(None)
):
    try:
        logger.info(f"👤 Adding attendee: {name}")

        if not name or not name.strip():
            logger.warning("⚠️ Missing name for attendee")
            return {"status": "error", "message": "Name is required"}

        if not photo and not audio:
            logger.warning("⚠️ At least photo or audio is required for attendee")
            return {"status": "error", "message": "Please provide either a photo or audio sample"}

        import re
        import unicodedata

        name_normalized = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')

        sanitized_name = re.sub(r'[^\w\s-]', '', name_normalized).strip()

        sanitized_name = re.sub(r'\s+', '_', sanitized_name)

        if not sanitized_name:
            sanitized_name = f"attendee_{uuid.uuid4().hex[:8]}"

        photo_saved = False
        audio_saved = False
        person_face_dir = None

        if photo and photo.filename and getattr(photo, 'size', None) and photo.size > 0:
            logger.info(f"📷 Processing photo for: {name}")

            allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
            file_extension = Path(photo.filename).suffix.lower()

            if file_extension not in allowed_extensions:
                logger.warning(f"⚠️ Invalid file type: {file_extension}")
                return {"status": "error", "message": "Invalid file type. Please upload JPG, PNG, or WebP"}

            person_face_dir = os.path.join(KNOWN_FACES_DIR, sanitized_name)
            os.makedirs(person_face_dir, exist_ok=True)

            photo_path = os.path.join(person_face_dir, f"profile{file_extension}")

            content = await photo.read()
            if len(content) == 0:
                logger.warning("⚠️ Empty photo file uploaded")
                return {"status": "error", "message": "Uploaded photo file is empty"}

            with open(photo_path, "wb") as buffer:
                buffer.write(content)

            img = cv2.imread(photo_path)
            if img is None:
                os.remove(photo_path)
                logger.warning("⚠️ Invalid image file")
                return {"status": "error", "message": "Invalid image file. Please upload a valid image."}

            try:
                faces = attendance_system.face_model.get(img)
                if not faces:
                    os.remove(photo_path)
                    logger.warning("⚠️ No face detected in image")
                    return {"status": "error", "message": "No face detected in the image. Please upload a clear face photo"}
                logger.info(f"✅ Face detected in photo: {len(faces)} faces")
            except Exception as e:
                logger.warning(f"⚠️ Face detection error: {e}")

            photo_saved = True
            logger.info(f"✅ Face photo saved for: {name} at {photo_path}")
        elif photo and photo.filename:
            logger.warning("⚠️ Empty photo file selected")

        if audio and audio.filename and getattr(audio, 'size', None) and audio.size > 0:
            logger.info(f"🎵 Processing audio file for: {name}")

            os.makedirs(AUDIO_SAMPLES_DIR, exist_ok=True)

            audio_content = await audio.read()
            if len(audio_content) == 0:
                logger.warning("⚠️ Empty audio file uploaded")
                return {"status": "error", "message": "Uploaded audio file is empty"}

            is_valid, audio_data, sample_rate, error_msg = validate_and_convert_audio(
                audio_content,
                audio.filename
            )

            if not is_valid:
                logger.error(f"❌ Invalid audio: {error_msg}")
                return {"status": "error", "message": f"Invalid audio file: {error_msg}"}

            safe_name = sanitized_name
            audio_path = os.path.join(AUDIO_SAMPLES_DIR, f"{safe_name}.wav")

            try:
                sf.write(audio_path, audio_data, sample_rate, subtype='PCM_16')

                if os.path.exists(audio_path):
                    file_size = os.path.getsize(audio_path)
                    if file_size < 1000:
                        os.remove(audio_path)
                        raise Exception("Generated audio file is too small")

                    logger.info(f"✅ Audio sample saved: {audio_path} (Size: {file_size/1024:.1f}KB)")
                    audio_saved = True
            except Exception as e:
                logger.error(f"❌ Failed to save audio: {e}")
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                return {"status": "error", "message": f"Failed to save audio: {str(e)}"}
        elif audio and audio.filename:
            logger.warning("⚠️ Empty audio file selected")

        if not photo_saved and person_face_dir and os.path.exists(person_face_dir):
            try:
                if not os.listdir(person_face_dir):
                    os.rmdir(person_face_dir)
                    logger.info(f"🧹 Cleaned up empty directory: {person_face_dir}")
            except Exception as e:
                logger.warning(f"⚠️ Could not clean up directory: {e}")

        if photo_saved:
            attendance_system.load_known_faces()
            logger.info(f"✅ Reloaded known faces after adding {name}")

        message = f"Attendee '{name}' registered successfully!"
        if photo_saved:
            message += " Face photo saved."
        if audio_saved:
            message += " Voice sample saved."

        logger.info(f"✅ Attendee added: {name} (Photo: {photo_saved}, Audio: {audio_saved})")
        return {
            "status": "success",
            "message": message,
            "person_name": name,
            "photo_saved": photo_saved,
            "audio_saved": audio_saved,
            "sanitized_name": sanitized_name
        }

    except Exception as e:
        logger.error(f"❌ Failed to add attendee: {e}")
        traceback.print_exc()
        if 'person_face_dir' in locals() and person_face_dir and os.path.exists(person_face_dir):
            if not os.listdir(person_face_dir):
                try:
                    os.rmdir(person_face_dir)
                except:
                    pass
        return {"status": "error", "message": f"Failed to add attendee: {str(e)}"}

@app.get("/attendance")
async def get_attendance():
    try:
        logger.debug("📊 Fetching attendance data")
        
        # FIX 2: Ensure we're using tracked_persons for attendance
        records_list = []
        present_count = 0
        
        # Track from known faces
        for person in attendance_system.all_required_persons:
            # Check if person is recognized in tracked_persons
            is_present = False
            first_seen = None
            last_seen = None
            
            for track_id, data in attendance_system.tracked_persons.items():
                if data['recognized'] and data['name'] == person:
                    is_present = True
                    first_seen = data['first_seen_time']
                    last_seen = data['last_seen_time']
                    break
            
            if is_present:
                status = 'Present'
                time_display = f"Detected: {first_seen.strftime('%H:%M:%S')}"
                present_count += 1
            else:
                status = 'Absent'
                time_display = 'Not detected'
            
            record = {
                'name': person,
                'time': time_display,
                'status': status
            }
            records_list.append(record)
        
        # Add any recognized but unknown persons
        for track_id, data in attendance_system.tracked_persons.items():
            if data['recognized'] and data['name'] == "Unknown":
                record = {
                    'name': f"Unknown_{track_id}",
                    'time': data['first_seen_time'].strftime('%H:%M:%S'),
                    'status': 'Unknown'
                }
                records_list.append(record)
        
        total_count = len(attendance_system.all_required_persons)
        attendance_rate = (present_count / total_count) * 100 if total_count > 0 else 0
        
        logger.debug(f"📈 Attendance summary: {present_count}/{total_count} present ({attendance_rate:.1f}%)")
        
        return {
            "status": "success",
            "attendance": records_list,
            "summary": {
                "total": total_count,
                "present": present_count,
                "absent": total_count - present_count,
                "attendance_rate": attendance_rate
            }
        }
    except Exception as e:
        logger.error(f"❌ Error in attendance endpoint: {e}")
        return {
            "status": "error", 
            "message": f"Failed to get attendance: {str(e)}",
            "attendance": [],
            "summary": {
                "total": 0,
                "present": 0,
                "absent": 0,
                "attendance_rate": 0
            }
        }

@app.get("/download_file/{meeting_id}/{file_type}")
async def download_file(meeting_id: str, file_type: str):
    try:
        meeting_folder = None
        folder_name_to_use = None
        
        for folder_name in os.listdir(MEETINGS_DATA_DIR):
            folder_path = os.path.join(MEETINGS_DATA_DIR, folder_name)
            if not os.path.isdir(folder_path):
                continue
            
            meeting_data_file = os.path.join(folder_path, f"{folder_name}_meeting_data.json")
            if os.path.exists(meeting_data_file):
                try:
                    with open(meeting_data_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if 'id' in data and data['id'].lower() == meeting_id.lower():
                            meeting_folder = folder_path
                            folder_name_to_use = folder_name
                            logger.info(f"✅ Found meeting folder by ID: {folder_name}")
                            break
                except Exception as e:
                    logger.warning(f"⚠️ Error reading meeting data: {e}")
                    continue
        
        if not meeting_folder:
            for folder_name in os.listdir(MEETINGS_DATA_DIR):
                folder_path = os.path.join(MEETINGS_DATA_DIR, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                
                if meeting_id.lower() in folder_name.lower():
                    meeting_folder = folder_path
                    folder_name_to_use = folder_name
                    logger.info(f"✅ Found meeting folder by name match: {folder_name}")
                    break
        
        if not meeting_folder:
            folders = [os.path.join(MEETINGS_DATA_DIR, f) for f in os.listdir(MEETINGS_DATA_DIR) 
                      if os.path.isdir(os.path.join(MEETINGS_DATA_DIR, f))]
            if folders:
                meeting_folder = max(folders, key=os.path.getmtime)
                folder_name_to_use = os.path.basename(meeting_folder)
                logger.warning(f"⚠️ Using most recent folder: {folder_name_to_use}")
        
        if not meeting_folder:
            raise HTTPException(status_code=404, detail=f"Meeting folder not found for ID: {meeting_id}")
        
        file_mappings = {
            "audio": f"{folder_name_to_use}_audio.wav",
            "summary": f"{folder_name_to_use}_summary.pdf",
            "transcript": f"{folder_name_to_use}_transcript.pdf",
            "attendance": f"{folder_name_to_use}_attendance.xlsx",
        }
        
        if file_type not in file_mappings:
            raise HTTPException(status_code=400, detail="Invalid file type")
        
        filename = file_mappings[file_type]
        file_path = os.path.join(meeting_folder, filename)
        
        if not os.path.exists(file_path):
            alternative_patterns = [
                f"{folder_name_to_use}_{file_type}.*",
                f"*{file_type}.*"
            ]
            
            for pattern in alternative_patterns:
                matches = glob.glob(os.path.join(meeting_folder, pattern))
                if matches:
                    file_path = matches[0]
                    filename = os.path.basename(file_path)
                    break
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail=f"File not found: {filename}")
        
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type='application/octet-stream'
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Download error: {e}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.get("/check_video_file")
async def check_video_file(folder: str):
    """Check if video file exists for a meeting folder"""
    try:
        folder_path = os.path.join(MEETINGS_DATA_DIR, folder)
        
        if not os.path.exists(folder_path):
            return JSONResponse({"exists": False, "message": "Folder not found"})
        
        # Look for video files with multiple patterns
        video_patterns = [
            f"{folder}_video.mp4",
            f"{folder}_video*.mp4",
            "*.mp4",
            "*.mkv",
            "*.mov"
        ]
        
        for pattern in video_patterns:
            video_files = glob.glob(os.path.join(folder_path, pattern))
            if video_files:
                # Get the most recent video file
                latest_video = max(video_files, key=os.path.getmtime)
                
                # Verify file is complete (not being written to)
                if os.path.exists(latest_video):
                    file_size = os.path.getsize(latest_video)
                    
                    # Wait a moment to ensure file is stable
                    import time
                    mtime1 = os.path.getmtime(latest_video)
                    time.sleep(0.5)
                    mtime2 = os.path.getmtime(latest_video)
                    
                    # If file hasn't been modified in last 0.5 seconds, it's likely complete
                    if mtime1 == mtime2 and file_size > 1024:  # At least 1KB
                        return JSONResponse({
                            "exists": True,
                            "path": latest_video,
                            "filename": os.path.basename(latest_video),
                            "size": file_size,
                            "size_mb": file_size / (1024 * 1024)
                        })
        
        return JSONResponse({"exists": False, "message": "No complete video file found"})
        
    except Exception as e:
        logger.error(f"❌ Error checking video file: {e}")
        return JSONResponse({"exists": False, "error": str(e)})

@app.get("/check_file_exists")
async def check_file_exists(folder: str, file: str):
    try:
        file_path = os.path.join(MEETINGS_DATA_DIR, folder, file)
        if os.path.exists(file_path):
            return JSONResponse({"exists": True, "path": file_path})
        else:
            folder_path = os.path.join(MEETINGS_DATA_DIR, folder)
            if os.path.exists(folder_path):
                for f in os.listdir(folder_path):
                    if file.lower() in f.lower():
                        return JSONResponse({"exists": True, "path": os.path.join(folder_path, f)})
            return JSONResponse({"exists": False}, status_code=404)
    except Exception as e:
        logger.error(f"❌ Error checking file: {e}")
        return JSONResponse({"exists": False, "error": str(e)}, status_code=500)

@app.get("/system-status")
async def get_system_status():
    global meeting_status
    
    recognized_count = len([p for p in attendance_system.tracked_persons.values() if p['recognized']])
    in_frame_count = len([p for p in attendance_system.tracked_persons.values() if p['in_frame']])
    
    meeting_status_data = meeting_manager.get_meeting_status()
    
    gpu_available = torch.cuda.is_available()
    
    logger.debug("🔍 System status checked")
    return {
        "status": "running",
        "camera_active": attendance_system.is_running,
        "known_persons": len(attendance_system.all_required_persons),
        "loaded_faces": len(attendance_system.known_embeddings),
        "tracked_persons": len(attendance_system.tracked_persons),
        "recognized_now": recognized_count,
        "in_frame_now": in_frame_count,
        "tracking_mode": attendance_system.tracking_mode,
        "camera_zoom": attendance_system.camera_zoom,
        "gpu_available": gpu_available,
        "session_start": attendance_system.session_start_time.isoformat(),
        "meeting_active": meeting_status_data['active'],
        "attendance_active": meeting_status['attendance_active'],
        "audio_recording_active": meeting_status['audio_recording_active'],
        "video_recording_active": meeting_status['video_recording_active'],
        "obs_connected": obs_controller.client is not None,
        "obs_state": obs_controller.state,
        "obs_status": obs_controller.get_status() if obs_controller.client else "Not connected",
    }

@app.post("/reset_tracking")
async def reset_tracking():
    logger.info("🔄 Resetting tracking data")
    attendance_system.tracked_persons.clear()
    attendance_system.person_attendance_state.clear()
    attendance_system.session_start_time = datetime.now()
    logger.info("✅ Tracking data reset, new session started")
    return {"status": "success", "message": "Tracking data reset, new session started"}

@app.post("/api/send_meeting_email")
async def send_meeting_email(
    meeting_id: str = Form(...),
    recipients: str = Form(None)
):
    try:
        meeting_status_data = meeting_manager.get_meeting_status()
        current_meeting = meeting_manager.current_meeting
        
        if not current_meeting or current_meeting['id'] != meeting_id:
            logger.error(f"❌ Meeting {meeting_id} not found or not active")
            return {"status": "error", "message": "Meeting not found or not active"}
        
        recipient_list = []
        
        if recipients:
            recipient_list = [r.strip() for r in recipients.split(',') if r.strip()]
        elif current_meeting.get('emails'):
            recipient_list = current_meeting['emails']
        elif meeting_manager.stored_emails:
            recipient_list = meeting_manager.stored_emails
        
        if not recipient_list:
            logger.error("❌ No email recipients specified")
            return {"status": "error", "message": "No email recipients specified. Please provide email addresses in meeting setup."}
        
        logger.info(f"📧 Sending email to: {recipient_list}")
        
        meeting_folder = current_meeting['folder']
        folder_name = os.path.basename(meeting_folder)
        
        attachments = []
        
        file_patterns = [
            f"{folder_name}_summary.pdf",
            f"{folder_name}_attendance.xlsx", 
            f"{folder_name}_transcript.pdf"
        ]

        for pattern in file_patterns:
            file_path = os.path.join(meeting_folder, pattern)
            if os.path.exists(file_path):
                attachments.append(file_path)
                logger.info(f"📎 Attaching: {os.path.basename(file_path)}")

        if not attachments:
            logger.error("❌ No meeting files found to send")
            return {"status": "error", "message": "No meeting files found to send"}
        
        subject = f"Meeting Summary - {meeting_id}"
        body = f"""Meeting Summary Report

Meeting Title: {current_meeting.get('title', 'Meeting')}
Meeting ID: {meeting_id}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Duration: {current_meeting.get('duration', 'N/A')}
Agenda: {current_meeting.get('agenda', 'N/A')}

Please find the meeting files attached.

Best regards,
Asgard Analytics - Intelligent Meeting Documentation
        """
        
        success = email_automator.send_email(
            recipients=recipient_list,
            subject=subject,
            body=body,
            attachments=attachments
        )
        
        if success:
            logger.info(f"✅ Email sent successfully to {len(recipient_list)} recipients")
            return {
                "status": "success",
                "message": f"Email sent successfully to {len(recipient_list)} recipients"
            }
        else:
            logger.error("❌ Failed to send email")
            return {"status": "error", "message": "Failed to send email"}
            
    except Exception as e:
        logger.error(f"❌ Error sending email: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.get("/api/get_config")
async def get_config():
    try:
        config_dict = email_automator.config
        
        safe_config = {
            "LLM": {
                "api_key": "********" if config_dict["LLM"]["api_key"] else "",
                "model": config_dict["LLM"]["model"],
                "base_url": config_dict["LLM"]["base_url"],
                "temperature": config_dict["LLM"]["temperature"],
                "max_tokens": config_dict["LLM"]["max_tokens"],
                "timeout": config_dict["LLM"]["timeout"]
            },
            "EMAIL": {
                "sender": config_dict["EMAIL"]["sender"],
                "password": "********" if config_dict["EMAIL"]["password"] else "",
                "smtp_server": config_dict["EMAIL"]["smtp_server"],
                "smtp_port": config_dict["EMAIL"]["smtp_port"],
                "use_tls": config_dict["EMAIL"]["use_tls"]
            }
        }
        
        return {
            "status": "success",
            "config": safe_config
        }
    except Exception as e:
        logger.error(f"❌ Failed to get config: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/test_diarization_enhanced")
async def test_diarization_enhanced(audio_file: UploadFile = File(...)):
    try:
        temp_file = f"temp_test_{uuid.uuid4().hex}.wav"
        with open(temp_file, "wb") as buffer:
            content = await audio_file.read()
            buffer.write(content)
        
        logger.info(f"🎙️ Testing enhanced diarization on: {audio_file.filename}")
        
        transcript_data = meeting_manager.diarizer.diarize_audio(temp_file, "Test agenda", use_manual_fallback=False)
        
        if os.path.exists(temp_file):
            os.remove(temp_file)
        
        if transcript_data:
            test_output = os.path.join(BASE_DIR, "test_diarization_output.json")
            with open(test_output, 'w', encoding='utf-8') as f:
                json.dump(transcript_data, f, indent=4, ensure_ascii=False)
            
            logger.info(f"✅ Test diarization completed. Output saved to: {test_output}")
            
            return {
                "status": "success",
                "message": "Diarization completed successfully",
                "output_file": test_output,
                "transcript_summary": {
                    "total_segments": len(transcript_data.get('transcript', [])),
                    "total_duration": transcript_data.get('total_duration', 0),
                    "unique_speakers": transcript_data.get('unique_speakers', 0),
                    "speakers": list(set(segment['speaker'] for segment in transcript_data.get('transcript', [])))
                },
                "transcript_sample": transcript_data.get('transcript', [])[:3] if transcript_data.get('transcript') else []
            }
        else:
            return {"status": "error", "message": "Diarization failed"}
            
    except Exception as e:
        logger.error(f"❌ Test diarization error: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/start_virtual_camera")
async def start_virtual_camera():
    """Manually start OBS Virtual Camera"""
    try:
        if obs_controller.client is None:
            return {
                "status": "error",
                "message": "OBS not connected"
            }
        
        logger.info("🎥 Manually starting OBS Virtual Camera...")
        
        if obs_controller.start_virtual_camera():
            return {
                "status": "success",
                "message": "OBS Virtual Camera started"
            }
        else:
            return {
                "status": "warning",
                "message": "Virtual camera might not be fully active"
            }
            
    except Exception as e:
        logger.error(f"❌ Error starting virtual camera: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🔴 Application shutting down, cleaning up resources...")
    
    try:
        # Stop video recording if active
        if meeting_status['video_recording_active']:
            await stop_video_recording()
        
        # Disconnect from OBS gracefully
        obs_controller.disconnect()
        
        temp_patterns = [
            "temp_*.wav",
            "temp_segment.wav",
            "temp_*.mp3",
            "*_temp.wav"
        ]
        
        cleaned_count = 0
        for pattern in temp_patterns:
            temp_files = glob.glob(pattern)
            for temp_file in temp_files:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        logger.info(f"🧹 Cleaned up temporary file: {temp_file}")
                        cleaned_count += 1
                except Exception as e:
                    logger.warning(f"⚠️ Failed to clean up {temp_file}: {e}")
        
        if cleaned_count > 0:
            logger.info(f"✅ Cleaned up {cleaned_count} temporary files")
        
        try:
            attendance_system.stop_camera()
            logger.info("✅ Camera system stopped")
        except:
            pass
        
        for ws in active_connections.copy():
            try:
                await ws.close()
                logger.info("✅ WebSocket connection closed")
            except:
                pass
        
        logger.info("✅ Shutdown cleanup completed")
    except Exception as e:
        logger.error(f"❌ Error during shutdown cleanup: {e}")

if __name__ == "__main__":
    logger.info("🚀 Starting Smart Meeting Manager Server with Enhanced OBS Controller...")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")