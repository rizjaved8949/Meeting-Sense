class SmartMeetingManager {
    constructor() {
        this.currentMeeting = null;
        this.meetingInterval = null;
        this.audioTimerInterval = null;
        this.audioStartTime = null;
        this.isCameraActive = false;
        this.transcripts = [];
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.recordingTimer = null;
        this.isProcessing = false;
        this.toastTimeout = null;
        this.confirmCallback = null;
        this.attendanceActive = false;
        this.lastMeetingId = null;
        this.audioStream = null;
        this.audioContext = null;
        this.audioProcessor = null;
        this.audioSource = null;
        this.audioRecordingChunks = [];
        this.audioRecordingTimer = null;
        this.audioRecordingStartTime = null;
        this.audioRecordingActive = false;
        
        // Video recording properties
        this.videoRecordingTimer = null;
        this.videoRecordingStartTime = null;
        this.videoRecordingActive = false;
        
        // Separate WebSocket connections
        this.attendanceWebSocket = null;
        this.liveFeedWebSocket = null;
        this.attendanceWebSocketHeartbeat = null;
        
        this.initializeApp();
    }

    initializeApp() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                this.setupApp();
            });
        } else {
            this.setupApp();
        }
    }

    setupApp() {
        try {
            console.log('🚀 Initializing Smart Meeting Manager...');
            const savedMeeting = localStorage.getItem('currentMeeting');
            const savedMeetingId = localStorage.getItem('lastMeetingId');
            
            if (savedMeeting) {
                try {
                    this.currentMeeting = JSON.parse(savedMeeting);
                    this.lastMeetingId = savedMeetingId;
                    console.log('✅ Restored previous meeting session:', this.lastMeetingId);
                } catch (e) {
                    console.warn('⚠️ Failed to parse saved meeting:', e);
                    localStorage.removeItem('currentMeeting');
                    localStorage.removeItem('lastMeetingId');
                }
            }
            
            this.initializeEventListeners();
            this.updateDateTime();
            
            setInterval(() => this.updateDateTime(), 1000);
            
            this.loadSystemStatus();
            this.initializeAttendanceSystem();
            
            console.log('✅ Smart Meeting Manager initialized successfully');
            
        } catch (error) {
            console.error('❌ Failed to initialize app:', error);
            this.showErrorPage(error);
        }
    }

    async checkFileStatus(meetingId, fileType) {
        try {
            const response = await fetch(`/download_file/${meetingId}/${fileType}`, {
                method: 'HEAD'
            });
            return response.ok;
        } catch (error) {
            return false;
        }
    }

    initializeEventListeners() {
        try {
            this.attachEventListener('create-meeting', 'click', () => this.createMeeting());
            this.attachEventListener('reset-app', 'click', () => this.resetMeeting());
            this.attachEventListener('start-attendance', 'click', () => this.startAttendance());
            this.attachEventListener('stop-attendance', 'click', () => this.stopAttendance());
            
            this.attachEventListener('start-recording', 'click', () => this.startMeetingAudioRecording());
            this.attachEventListener('stop-recording', 'click', () => this.stopMeetingAudioRecording());
            
            this.attachEventListener('camera-source', 'change', (e) => this.handleCameraSourceChange(e));
            this.attachEventListener('refresh-attendance', 'click', () => this.refreshAttendance());
            this.attachEventListener('system-status', 'click', () => this.showSystemStatus());
            this.attachEventListener('add-attendee-form', 'submit', (e) => this.addAttendee(e));
            this.attachEventListener('attendee-photo', 'change', (e) => this.previewImage(e));
            this.attachEventListener('remove-preview', 'click', () => this.removeImagePreview());
            this.attachEventListener('summary-download-pdf', 'click', () => this.downloadSummary());
            this.attachEventListener('summary-email-pdf', 'click', () => this.emailSummary());
            this.attachEventListener('summary-download-excel', 'click', () => this.downloadAttendance());
            this.attachEventListener('summary-close-popup', 'click', () => this.hideSummaryPopup());
            this.attachEventListener('summary-download-transcript', 'click', () => this.downloadTranscript());
            this.attachEventListener('summary-download-audio', 'click', () => this.downloadAudio());
            this.attachEventListener('summary-download-video', 'click', () => this.downloadVideo());
            
            // Video recording event listeners
            this.attachEventListener('start-video-recording', 'click', () => this.startVideoRecording());
            this.attachEventListener('stop-video-recording', 'click', () => this.stopVideoRecording());
            
            this.attachEventListener('audio-upload-option', 'click', () => this.uploadAudioFile());
            this.attachEventListener('audio-record-option', 'click', () => this.startAudioCapture());
            this.attachEventListener('stop-audio-recording', 'click', () => this.stopAudioRecording());
            this.attachEventListener('cancel-audio-recording', 'click', () => this.cancelAudioRecording());
            this.attachEventListener('remove-audio', 'click', () => this.removeAudioPreview());
            this.attachEventListener('end-meeting', 'click', () => this.endMeeting());

            // OBS setup instructions button
            this.attachEventListener('obs-setup-instructions', 'click', () => this.showOBSSetupInstructions());
            
            // OBS status check button
            this.attachEventListener('check-obs-status', 'click', () => this.checkOBSStatus());

            this.setupFileUpload();
            this.setupFormValidation();

            console.log('✅ Event listeners initialized');

        } catch (error) {
            console.error('❌ Error initializing event listeners:', error);
        }
    }

    // UPDATE: Connect to attendance WebSocket
    async connectAttendanceWebSocket() {
        if (this.attendanceWebSocket && this.attendanceWebSocket.readyState === WebSocket.OPEN) {
            return;
        }
        
        try {
            const wsUrl = window.location.origin.replace('http://', 'ws://').replace('https://', 'wss://') + '/ws';
            this.attendanceWebSocket = new WebSocket(wsUrl);
            
            this.attendanceWebSocket.onopen = () => {
                console.log('✅ Attendance WebSocket connected');
                this.startWebSocketHeartbeat();
            };
            
            this.attendanceWebSocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleAttendanceWebSocketMessage(data);
                } catch (error) {
                    console.error('WebSocket message error:', error);
                }
            };
            
            this.attendanceWebSocket.onclose = () => {
                console.log('❌ Attendance WebSocket disconnected');
                this.attendanceWebSocket = null;
                setTimeout(() => this.connectAttendanceWebSocket(), 3000);
            };
            
            this.attendanceWebSocket.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
            
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
        }
    }
    
    // UPDATE: Connect to live feed WebSocket - Clear previous feed first
    async connectLiveFeedWebSocket() {
        // FIX: Clear any existing feed first
        this.clearCameraFeed();
        
        if (this.liveFeedWebSocket && this.liveFeedWebSocket.readyState === WebSocket.OPEN) {
            this.liveFeedWebSocket.close();
        }
        
        try {
            const wsUrl = window.location.origin.replace('http://', 'ws://').replace('https://', 'wss://') + '/ws_live_feed';
            this.liveFeedWebSocket = new WebSocket(wsUrl);
            
            this.liveFeedWebSocket.onopen = () => {
                console.log('✅ Live Feed WebSocket connected');
                
                // Update UI to show OBS feed
                const overlay = document.getElementById('video-overlay');
                if (overlay) {
                    overlay.style.display = 'none';
                }
                
                this.updateFeedStatus('OBS Virtual Camera - Live Feed');
            };
            
            this.liveFeedWebSocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleLiveFeedWebSocketMessage(data);
                } catch (error) {
                    console.error('Live feed WebSocket message error:', error);
                }
            };
            
            this.liveFeedWebSocket.onclose = () => {
                console.log('❌ Live Feed WebSocket disconnected');
                this.liveFeedWebSocket = null;
                
                // Show OBS stopped message
                const overlay = document.getElementById('video-overlay');
                if (overlay) {
                    overlay.innerHTML = `
                        <span style="font-size: 2.5em;">📷</span>
                        <p>OBS Feed Disconnected</p>
                        <small>Recording stopped or OBS not available</small>
                    `;
                    overlay.style.display = 'flex';
                }
            };
            
            this.liveFeedWebSocket.onerror = (error) => {
                console.error('Live feed WebSocket error:', error);
                this.updateFeedStatus('Live feed disconnected');
            };
            
        } catch (error) {
            console.error('Failed to connect live feed WebSocket:', error);
        }
    }
    
    // SEPARATE: Handle attendance WebSocket messages
    handleAttendanceWebSocketMessage(data) {
        if (data.type === 'pong') {
            console.log('❤️ Attendance WebSocket heartbeat received');
            return;
        }
        
        if (data.type === 'frame') {
            this.updateCameraFeed(data.frame);
            
            if (data.message) {
                this.updateFeedStatus(data.message);
            }
            
            if (data.person_count !== undefined) {
                this.updateAttendanceStats(data);
            }
        }
    }
    
    // SEPARATE: Handle live feed WebSocket messages
    handleLiveFeedWebSocketMessage(data) {
        if (data.type === 'frame') {
            this.updateCameraFeed(data.frame);
            
            if (data.message) {
                this.updateFeedStatus(data.message + ' - RECORDING');
            }
        }
    }
    
    // UPDATE: Start WebSocket heartbeat
    startWebSocketHeartbeat() {
        if (this.attendanceWebSocketHeartbeat) {
            clearInterval(this.attendanceWebSocketHeartbeat);
        }
        
        this.attendanceWebSocketHeartbeat = setInterval(() => {
            if (this.attendanceWebSocket && this.attendanceWebSocket.readyState === WebSocket.OPEN) {
                try {
                    this.attendanceWebSocket.send(JSON.stringify({ type: 'ping' }));
                    console.log('❤️ Heartbeat ping sent');
                } catch (e) {
                    console.warn('Heartbeat send error:', e);
                }
            }
        }, 30000);
    }

    // NEW METHOD: Check OBS readiness
    async checkOBSReady() {
        try {
            // Check if OBS is connected
            const statusResponse = await fetch('/video_recording_status');
            const statusResult = await statusResponse.json();
            
            if (!statusResult.obs_connected) {
                const connectResponse = await fetch('/start_video_recording', {
                    method: 'POST'
                });
                const connectResult = await connectResponse.json();
                
                if (connectResult.status !== 'success') {
                    this.showToast('OBS not ready. Please check OBS is running with WebSocket enabled.', 'error');
                    return false;
                }
            }
            
            return true;
        } catch (error) {
            this.showToast('OBS connection check failed', 'error');
            return false;
        }
    }

    // UPDATED: Show simplified OBS instructions
    showOBSSetupInstructions() {
        const instructions = `
✅ SIMPLE OBS SETUP (AUTO-DETECTION):

BEFORE STARTING VIDEO RECORDING:

1. Open OBS Studio manually
2. OBS will auto-detect your camera and microphone
3. If not auto-detected:
   - Click '+' in Sources
   - Add 'Video Capture Device' → Select your camera
   - Add 'Audio Input Capture' → Select your microphone
4. Configure WebSocket:
   - Tools → WebSocket Server Settings
   - Enable WebSocket server
   - Port: 4455
   - No password (uncheck "Enable Authentication")
   - Click OK
5. Close OBS Studio

6. Now in this app, click "Start Recording"

THE APP WILL:
- Connect to OBS WebSocket
- Start Virtual Camera (for live feed)
- Start recording from your OBS scene

💡 Virtual Camera = Live feed display
💡 Recording = Actual OBS scene with your camera/mic
💡 OBS handles all camera/audio configuration automatically
`;
        
        alert(instructions);
    }

    // NEW METHOD: Check OBS status
    async checkOBSStatus() {
        try {
            const response = await fetch('/video_recording_status');
            const result = await response.json();
            
            let statusMessage = `OBS Status:\n`;
            statusMessage += `Video Recording: ${result.video_recording_active ? 'ACTIVE' : 'INACTIVE'}\n`;
            statusMessage += `OBS State: ${result.obs_state || 'Unknown'}\n`;
            statusMessage += `OBS Status: ${result.obs_status || 'N/A'}\n`;
            
            if (result.recording_path) {
                statusMessage += `Recording Path: ${result.recording_path}\n`;
            }
            
            alert(statusMessage);
        } catch (error) {
            console.error('OBS status check error:', error);
            this.showToast('Failed to check OBS status', 'error');
        }
    }

    // UPDATED: Start video recording with OBS check
    async startVideoRecording() {
        if (this.isProcessing) return;
        
        if (!this.currentMeeting) {
            this.showToast('Please create a meeting first', 'error');
            return;
        }
        
        // Check OBS readiness first
        if (!await this.checkOBSReady()) {
            return;
        }
        
        this.isProcessing = true;
        this.showLoading('Starting video recording with OBS...');
        
        try {
            const response = await fetch('/start_video_recording', {
                method: 'POST'
            });
            const result = await response.json();
            
            if (result.status === 'success') {
                this.videoRecordingActive = true;
                this.updateVideoRecordingUI(true);
                this.startVideoRecordingTimer();
                this.showToast('Video recording started with OBS', 'success');
                // NO CAMERA CONNECTION - OBS handles its own sources
            } else {
                this.showToast(`Video recording failed: ${result.message}`, 'error');
            }
        } catch (error) {
            console.error('Start video recording error:', error);
            this.showToast('Failed to start video recording', 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    // UPDATED: Stop video recording - removed camera conflict
    async stopVideoRecording() {
        if (this.isProcessing) return;
        
        this.isProcessing = true;
        this.showLoading('Stopping video recording and cleaning up OBS...');
        
        try {
            const response = await fetch('/stop_video_recording', {
                method: 'POST'
            });
            const result = await response.json();
            
            if (result.status === 'success') {
                this.videoRecordingActive = false;
                this.updateVideoRecordingUI(false);
                this.stopVideoRecordingTimer();
                
                // ✅ REMOVED: Don't stop camera for OBS - OBS handles it internally
                
                this.showToast('Video recording stopped', 'success');
            } else {
                this.showToast(`Video recording stop failed: ${result.message}`, 'error');
            }
        } catch (error) {
            console.error('Stop video recording error:', error);
            this.showToast('Failed to stop video recording', 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    // UPDATED: Update video recording UI with OBS status
    updateVideoRecordingUI(isActive) {
        // Update video status indicator in camera feed header
        const videoStatus = document.getElementById('video-recording-status');
        if (videoStatus) {
            if (isActive) {
                videoStatus.className = 'video-status recording';
                videoStatus.textContent = '● REC';
                videoStatus.setAttribute('aria-label', 'Video recording active');
            } else {
                videoStatus.className = 'video-status idle';
                videoStatus.textContent = '●';
                videoStatus.setAttribute('aria-label', 'Video recording idle');
            }
        }
        
        // Show/hide video timer
        const videoTimer = document.getElementById('video-recording-timer');
        if (videoTimer) {
            videoTimer.style.display = isActive ? 'block' : 'none';
        }
        
        // Show/hide OBS status indicator
        const obsStatus = document.getElementById('obs-status');
        if (obsStatus) {
            obsStatus.style.display = isActive ? 'block' : 'none';
        }
    }

    // Start video recording timer
    startVideoRecordingTimer() {
        if (this.videoRecordingTimer) {
            clearInterval(this.videoRecordingTimer);
        }
        
        this.videoRecordingStartTime = Date.now();
        this.videoRecordingTimer = setInterval(() => {
            const elapsed = Date.now() - this.videoRecordingStartTime;
            const hours = Math.floor(elapsed / 3600000);
            const minutes = Math.floor((elapsed % 3600000) / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);
            
            const timerElement = document.getElementById('video-recording-timer-text');
            if (timerElement) {
                timerElement.textContent = 
                    `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }
        }, 1000);
    }

    // Stop video recording timer
    stopVideoRecordingTimer() {
        if (this.videoRecordingTimer) {
            clearInterval(this.videoRecordingTimer);
            this.videoRecordingTimer = null;
        }
        
        const timerElement = document.getElementById('video-recording-timer-text');
        if (timerElement) {
            timerElement.textContent = '00:00:00';
        }
    }

    // UPDATE: Stop attendance system - PROPERLY clean up camera feed
    async stopAttendance() {
        if (this.isProcessing) return;
        
        this.isProcessing = true;
        this.showLoading('Stopping attendance system...');

        try {
            // 1. Stop attendance via API
            const response = await fetch('/stop_attendance', {
                method: 'POST'
            });
            
            const result = await response.json();
            
            if (result.status === 'success') {
                this.attendanceActive = false;
                this.toggleAttendanceButtons(false);
                this.stopAttendanceTimer();
                
                // FIX: Close attendance WebSocket AND stop camera feed
                if (this.attendanceWebSocket) {
                    this.attendanceWebSocket.close();
                    this.attendanceWebSocket = null;
                }
                
                // FIX: Clear the video feed display
                this.clearCameraFeed();
                
                // FIX: Stop the camera via API to release it
                await this.stopAttendanceCamera();
                
                this.showToast('Attendance system stopped', 'success');
            } else {
                this.showToast(result.message || 'Failed to stop attendance', 'error');
            }
        } catch (error) {
            console.error('Stop attendance error:', error);
            this.showToast('Failed to stop attendance', 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    // NEW METHOD: Stop attendance camera feed
    async stopAttendanceCamera() {
        try {
            const response = await fetch('/stop_camera', {
                method: 'POST'
            });
            return await response.json();
        } catch (error) {
            console.warn('Stop camera API error:', error);
            return { status: 'error', message: 'Failed to stop camera' };
        }
    }

    // NEW METHOD: Clear camera feed display
    clearCameraFeed() {
        const canvas = document.getElementById('video-canvas');
        const overlay = document.getElementById('video-overlay');
        
        if (canvas) {
            const ctx = canvas.getContext('2d');
            // Clear canvas
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            // Draw black background
            ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
        }
        
        // Show overlay with camera stopped message
        if (overlay) {
            overlay.innerHTML = `
                <span style="font-size: 2.5em;">📷</span>
                <p>Camera Stopped</p>
                <small>Click "Start Attendance" to begin face recognition</small>
            `;
            overlay.style.display = 'flex';
        }
        
        // Reset camera status
        const cameraStatus = document.getElementById('camera-status');
        const cameraStatusText = document.getElementById('camera-status-text');
        if (cameraStatus) cameraStatus.className = 'status-dot';
        if (cameraStatusText) cameraStatusText.textContent = 'Camera Offline';
        
        // Reset statistics
        const faceCount = document.getElementById('face-count');
        const presentCount = document.getElementById('present-count');
        const attendanceRate = document.getElementById('attendance-rate-live');
        
        if (faceCount) faceCount.textContent = '0 Detected';
        if (presentCount) presentCount.textContent = '0 Present';
        if (attendanceRate) attendanceRate.textContent = '0%';
    }

    // UPDATE: Start meeting audio and video recording - Ensure attendance is properly stopped
    async startMeetingAudioRecording() {
        if (this.isProcessing) return;
        
        if (!this.currentMeeting) {
            this.showToast('Please create a meeting first', 'error');
            return;
        }
        
        // FIX: Stop attendance FIRST and clear its feed
        if (this.attendanceActive) {
            this.showToast('Stopping attendance to start recording...', 'warning');
            await this.stopAttendance();  // This now properly cleans up
        }
        
        this.isProcessing = true;
        this.showLoading('Starting recordings...');

        try {
            // Step 1: Start OBS video recording via API
            const videoResponse = await fetch('/start_video_recording', {
                method: 'POST'
            });
            const videoResult = await videoResponse.json();
            
            // Step 2: Start audio recording via API
            const audioResponse = await fetch('/start_audio_recording', {
                method: 'POST'
            });
            const audioResult = await audioResponse.json();
            
            // Step 3: Handle results
            if (audioResult.status === 'success' && videoResult.status === 'success') {
                this.audioRecordingActive = true;
                this.videoRecordingActive = true;
                this.toggleRecordingButtons(true);
                this.startVideoRecordingTimer();
                this.updateVideoRecordingUI(true);
                
                // FIX: Clear any previous camera display
                this.clearCameraFeed();
                
                // FIX: Connect to live feed WebSocket for OBS Virtual Camera
                // Wait a moment for OBS to be ready
                setTimeout(() => {
                    this.connectLiveFeedWebSocket();
                    
                    // Update feed status
                    const overlay = document.getElementById('video-overlay');
                    if (overlay) {
                        overlay.innerHTML = `
                            <span style="font-size: 2.5em;">🎥</span>
                            <p>OBS Recording Active</p>
                            <small>Live feed from OBS Virtual Camera</small>
                        `;
                        overlay.style.display = 'flex';
                    }
                    
                    // Update camera status
                    const cameraStatusText = document.getElementById('camera-status-text');
                    if (cameraStatusText) cameraStatusText.textContent = 'OBS Virtual Camera';
                }, 2000);
                
                this.showToast('Audio and video recording started successfully', 'success');
            } else {
                this.showToast(`Partial success: Audio=${audioResult.status}, Video=${videoResult.status}`, 'warning');
            }
        } catch (error) {
            console.error('Start recording error:', error);
            this.showToast('Failed to start recording', 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    // UPDATE: Stop meeting audio and video recording - Re-enable attendance properly
    async stopMeetingAudioRecording() {
        if (this.isProcessing) return;
        
        this.isProcessing = true;
        this.showLoading('Stopping recordings...');

        try {
            // Step 1: Stop video recording
            const videoResponse = await fetch('/stop_video_recording', {
                method: 'POST'
            });
            const videoResult = await videoResponse.json();
            
            // Step 2: Stop audio recording
            const audioResponse = await fetch('/stop_audio_recording', {
                method: 'POST'
            });
            const audioResult = await audioResponse.json();
            
            // Step 3: Handle results
            if (audioResult.status === 'success' && videoResult.status === 'success') {
                this.audioRecordingActive = false;
                this.videoRecordingActive = false;
                this.toggleRecordingButtons(false);
                this.stopAudioRecordingTimer();
                this.stopVideoRecordingTimer();
                this.updateVideoRecordingUI(false);
                
                // FIX: Close live feed WebSocket
                if (this.liveFeedWebSocket) {
                    this.liveFeedWebSocket.close();
                    this.liveFeedWebSocket = null;
                }
                
                // FIX: Clear OBS camera feed display
                this.clearCameraFeed();
                
                // FIX: Show ready for attendance message
                const overlay = document.getElementById('video-overlay');
                if (overlay) {
                    overlay.innerHTML = `
                        <span style="font-size: 2.5em;">📷</span>
                        <p>Ready for Attendance</p>
                        <small>Click "Start Attendance" to begin face recognition</small>
                    `;
                    overlay.style.display = 'flex';
                }
                
                // Update camera status
                const cameraStatusText = document.getElementById('camera-status-text');
                if (cameraStatusText) cameraStatusText.textContent = 'Camera Offline';
                
                this.showToast('Recordings stopped successfully', 'success');
            } else {
                this.showToast(`Partial stop: Audio=${audioResult.status}, Video=${videoResult.status}`, 'warning');
            }
        } catch (error) {
            console.error('Stop recording error:', error);
            this.showToast('Failed to stop recording', 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    // UPDATED: Toggle buttons to prevent conflicts - Added attendance button disabling
    toggleRecordingButtons(isActive) {
        const startRecordingBtn = document.getElementById('start-recording');
        const stopRecordingBtn = document.getElementById('stop-recording');
        
        if (startRecordingBtn) {
            startRecordingBtn.style.display = isActive ? 'none' : 'block';
            startRecordingBtn.disabled = isActive;
        }
        if (stopRecordingBtn) {
            stopRecordingBtn.style.display = isActive ? 'block' : 'none';
            stopRecordingBtn.disabled = !isActive;
        }
        
        // FIX: Disable attendance buttons when recording is active
        const startAttendanceBtn = document.getElementById('start-attendance');
        const stopAttendanceBtn = document.getElementById('stop-attendance');
        
        if (startAttendanceBtn) {
            startAttendanceBtn.disabled = isActive;
            if (isActive) {
                startAttendanceBtn.title = 'Stop recording first';
                startAttendanceBtn.classList.add('disabled');
                // Actually stop attendance if it's running
                if (this.attendanceActive) {
                    this.stopAttendance();
                }
            } else {
                startAttendanceBtn.title = 'Start Attendance';
                startAttendanceBtn.classList.remove('disabled');
            }
        }
        
        this.updateVideoRecordingUI(isActive);
    }

    // UPDATE: Start attendance system
    async startAttendance() {
        if (this.isProcessing) return;
        
        // Check if video recording is active
        if (this.videoRecordingActive) {
            this.showToast('Cannot start attendance while video recording is active', 'error');
            return;
        }
        
        this.isProcessing = true;
        this.showLoading('Starting attendance system...');

        try {
            const response = await fetch('/start_attendance', {
                method: 'POST'
            });
            
            const result = await response.json();
            
            if (result.status === 'success') {
                this.attendanceActive = true;
                this.toggleAttendanceButtons(true);
                this.startAttendanceTimer();
                
                // Connect to attendance WebSocket
                await this.connectAttendanceWebSocket();
                
                this.showToast('Attendance system started', 'success');
            } else {
                this.showToast(result.message || 'Failed to start attendance', 'error');
            }
        } catch (error) {
            console.error('Start attendance error:', error);
            this.showToast('Failed to start attendance', 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    // UPDATE: Toggle attendance buttons
    toggleAttendanceButtons(isActive) {
        const startBtn = document.getElementById('start-attendance');
        const stopBtn = document.getElementById('stop-attendance');
        
        if (startBtn) {
            startBtn.style.display = isActive ? 'none' : 'block';
            startBtn.disabled = isActive;
        }
        if (stopBtn) {
            stopBtn.style.display = isActive ? 'block' : 'none';
            stopBtn.disabled = !isActive;
        }
        
        // Disable recording buttons when attendance is active
        const startRecordingBtn = document.getElementById('start-recording');
        const stopRecordingBtn = document.getElementById('stop-recording');
        
        if (startRecordingBtn) {
            startRecordingBtn.disabled = isActive;
            if (isActive) {
                startRecordingBtn.title = 'Stop attendance first';
                startRecordingBtn.classList.add('disabled');
            } else {
                startRecordingBtn.title = 'Start Recording';
                startRecordingBtn.classList.remove('disabled');
            }
        }
    }

    // UPDATE: Cleanup method to ensure proper camera release
    cleanupCameras() {
        // Clear both camera feeds
        this.clearCameraFeed();
        
        // Stop attendance if active
        if (this.attendanceActive) {
            this.stopAttendance();
        }
        
        // Close WebSocket connections
        if (this.attendanceWebSocket) {
            this.attendanceWebSocket.close();
            this.attendanceWebSocket = null;
        }
        
        if (this.liveFeedWebSocket) {
            this.liveFeedWebSocket.close();
            this.liveFeedWebSocket = null;
        }
        
        // Show camera stopped message
        const overlay = document.getElementById('video-overlay');
        if (overlay) {
            overlay.innerHTML = `
                <span style="font-size: 2.5em;">📷</span>
                <p>Camera System Ready</p>
                <small>Click "Start Attendance" or "Start Recording"</small>
            `;
            overlay.style.display = 'flex';
        }
    }

    attachEventListener(id, event, handler) {
        const element = document.getElementById(id);
        if (element) {      
            element.addEventListener(event, handler);
        } else {
            console.warn(`⚠️ Element with id '${id}' not found`);
        }
    }

    setupFileUpload() {
        const uploadArea = document.getElementById('upload-area');
        if (uploadArea) {
            uploadArea.addEventListener('click', () => {
                document.getElementById('attendee-photo').click();
            });
            
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.classList.add('dragover');
            });
            
            uploadArea.addEventListener('dragleave', () => {
                uploadArea.classList.remove('dragover');
            });
            
            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.classList.remove('dragover');
                
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    const fileInput = document.getElementById('attendee-photo');
                    fileInput.files = files;
                    const event = new Event('change', { bubbles: true });
                    fileInput.dispatchEvent(event);
                }
            });
        }
    }

    setupFormValidation() {
        const meetingTitle = document.getElementById('meeting-title');
        const meetingAgenda = document.getElementById('meeting-agenda');
        
        if (meetingTitle) {
            meetingTitle.addEventListener('input', () => this.validateField(meetingTitle));
        }
        
        if (meetingAgenda) {
            meetingAgenda.addEventListener('input', () => this.validateField(meetingAgenda));
        }
    }

    validateField(field) {
        const value = field.value.trim();
        const parent = field.closest('.input-group');
        
        if (!parent) return;
        
        if (!value) {
            parent.classList.add('error');
        } else {
            parent.classList.remove('error');
        }
    }

    updateDateTime() {
        try {
            const now = new Date();
            const timeElement = document.getElementById('current-time');
            
            if (timeElement) {
                timeElement.textContent = now.toLocaleTimeString('en-US', { 
                    hour12: false,
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit'
                });
            }
        } catch (error) {
            console.error('Error updating date/time:', error);
        }
    }

    disconnectWebSocket() {
        if (this.attendanceWebSocket) {
            if (this.attendanceWebSocketHeartbeat) {
                clearInterval(this.attendanceWebSocketHeartbeat);
                this.attendanceWebSocketHeartbeat = null;
            }
            
            try {
                if (this.attendanceWebSocket.readyState === WebSocket.OPEN) {
                    this.attendanceWebSocket.close();
                }
            } catch (error) {
                console.error('Error closing WebSocket:', error);
            }
            this.attendanceWebSocket = null;
        }
        
        if (this.liveFeedWebSocket) {
            try {
                if (this.liveFeedWebSocket.readyState === WebSocket.OPEN) {
                    this.liveFeedWebSocket.close();
                }
            } catch (error) {
                console.error('Error closing live feed WebSocket:', error);
            }
            this.liveFeedWebSocket = null;
        }
    }

    updateFeedStatus(message) {
        const feedStatus = document.getElementById('feed-status');
        if (feedStatus) {
            feedStatus.textContent = message;
            feedStatus.style.display = 'block';
            
            setTimeout(() => {
                feedStatus.style.display = 'none';
            }, 3000);
        }
    }

    updateCameraFeed(frameData) {
        const canvas = document.getElementById('video-canvas');
        const overlay = document.getElementById('video-overlay');
        const container = document.querySelector('.video-container');
        
        if (!canvas || !container) return;
        
        const ctx = canvas.getContext('2d');
        const img = new Image();
        
        img.onload = () => {
            const containerWidth = container.clientWidth;
            const containerHeight = container.clientHeight;
            
            canvas.width = containerWidth;
            canvas.height = containerHeight;
            
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            const imgAspect = img.width / img.height;
            const containerAspect = containerWidth / containerHeight;
            
            let drawWidth, drawHeight, offsetX, offsetY;
            
            if (imgAspect > containerAspect) {
                drawWidth = containerWidth;
                drawHeight = containerWidth / imgAspect;
                offsetX = 0;
                offsetY = (containerHeight - drawHeight) / 2;
            } else {
                drawHeight = containerHeight;
                drawWidth = containerHeight * imgAspect;
                offsetX = (containerWidth - drawWidth) / 2;
                offsetY = 0;
            }
            
            ctx.drawImage(img, offsetX, offsetY, drawWidth, drawHeight);
            
            if (overlay) overlay.style.display = 'none';
        };
        
        img.onerror = () => {
            console.error('Failed to load camera frame');
            if (overlay) overlay.style.display = 'flex';
        };
        
        img.src = `data:image/jpeg;base64,${frameData}`;
    }

    updateAttendanceStats(data) {
        const faceCountElement = document.getElementById('face-count');
        const presentCountElement = document.getElementById('present-count');
        const attendanceRateElement = document.getElementById('attendance-rate-live');
        const cameraStatus = document.getElementById('camera-status');
        const cameraStatusText = document.getElementById('camera-status-text');

        if (faceCountElement) {
            faceCountElement.textContent = `${data.person_count || 0} Detected`;
        }

        if (presentCountElement) {
            presentCountElement.textContent = `${data.recognized_count || 0} Present`;
        }

        if (attendanceRateElement) {
            const total = data.person_count || 1;
            const present = data.recognized_count || 0;
            const rate = Math.round((present / total) * 100);
            attendanceRateElement.textContent = `${rate}%`;
        }

        if (cameraStatus && cameraStatusText) {
            cameraStatus.className = 'status-dot connected';
            cameraStatusText.textContent = 'Camera Live';
        }
    }

    handleCameraSourceChange(e) {
        const cameraSource = e.target.value;
        const urlInput = document.getElementById('camera-url-input');
        
        if (urlInput) {
            if (cameraSource === 'url') {
                urlInput.style.display = 'block';
            } else {
                urlInput.style.display = 'none';
            }
        }
    }

    getCameraSource() {
        const cameraSourceSelect = document.getElementById('camera-source');
        if (!cameraSourceSelect) return "0";
        
        const selectedValue = cameraSourceSelect.value;
        
        if (selectedValue === 'url') {
            const urlInput = document.getElementById('camera-url');
            const url = urlInput ? urlInput.value.trim() : "";
            
            if (url && (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('rtsp://'))) {
                return url;
            } else if (!url) {
                this.showToast('Please enter a camera URL', 'error');
                return null;
            } else {
                this.showToast('Please enter a valid camera URL (http://, https://, or rtsp://)', 'error');
                return null;
            }
        }
        
        return selectedValue;
    }

    async refreshAttendance() {
        if (this.isProcessing) return;
        
        this.isProcessing = true;

        try {
            const response = await fetch('/attendance');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();

            if (result.status === 'success') {
                this.updateAttendanceList(result.attendance);
                this.updateAttendanceSummary(result.summary);
            } else {
                console.error('Attendance API error:', result.message);
                this.showToast(result.message || 'Failed to refresh attendance', 'error');
            }
        } catch (error) {
            console.error('Refresh attendance error:', error);
            this.showToast('Failed to refresh attendance data', 'error');
        } finally {
            this.isProcessing = false;
        }
    }

    updateAttendanceList(attendance) {
        const container = document.getElementById('attendance-list');
        if (!container) {
            console.error('Attendance list container not found');
            return;
        }
        
        if (!attendance || attendance.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <span style="font-size: 2em;">👤⏰</span>
                    <p>No attendance data</p>
                    <small>Start attendance tracking</small>
                </div>
            `;
            return;
        }

        container.innerHTML = attendance.map(person => {
            const status = person.status || 'Absent';
            const statusClass = status.toLowerCase().replace(' ', '-');
            const timeDisplay = person.time || 'Not detected';
            
            return `
                <div class="attendee-item ${statusClass}">
                    <div class="attendee-info">
                        <div class="attendee-name">${person.name || 'Unknown'}</div>
                        <div class="attendee-time">${timeDisplay}</div>
                    </div>
                    <div class="attendee-details">
                        <span class="attendee-status status-${statusClass}">
                            ${status}
                        </span>
                    </div>
                </div>
            `;
        }).join('');
    }

    updateAttendanceSummary(summary) {
        const totalCount = document.getElementById('total-count');
        const presentCount = document.getElementById('present-count-summary');
        const attendanceRate = document.getElementById('attendance-rate');

        if (totalCount) totalCount.textContent = summary.total || 0;
        if (presentCount) presentCount.textContent = summary.present || 0;
        
        const rate = Math.round(summary.attendance_rate || 0);
        if (attendanceRate) attendanceRate.textContent = `${rate}%`;
    }

    async createMeeting() {
        if (this.isProcessing) return;
        
        const titleInput = document.getElementById('meeting-title');
        const agendaInput = document.getElementById('meeting-agenda');
        
        if (!titleInput || !agendaInput) {
            this.showToast('Meeting form elements not found', 'error');
            return;
        }

        const title = titleInput.value.trim();
        const agenda = agendaInput.value.trim();

        if (!title) {
            this.showToast('Please enter a meeting title', 'error');
            titleInput.focus();
            return;
        }

        this.isProcessing = true;
        this.showLoading('Creating meeting...');

        try {
            const emailInput = document.getElementById('meeting-emails');
            const emails = emailInput ? emailInput.value.trim() : '';
            
            const formData = new FormData();
            formData.append('title', title);
            formData.append('agenda', agenda);
            formData.append('emails', emails);

            const response = await fetch('/create_meeting', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const result = await response.json();

            if (result.status === 'success') {
                this.currentMeeting = result.meeting;
                this.lastMeetingId = result.meeting.id;
                
                localStorage.setItem('lastMeetingId', this.lastMeetingId);
                localStorage.setItem('currentMeeting', JSON.stringify(this.currentMeeting));
                
                this.updateMeetingInfo();
                this.startMeetingTimer();
                
                this.attendanceActive = false;
                this.audioRecordingActive = false;
                this.videoRecordingActive = false;
                
                this.showMeetingInitialControls();
                
                this.showToast('Meeting session created. You can start Live Attendance.', 'success');
                
                console.log(`✅ Meeting created with ID: ${this.lastMeetingId}`);
            } else {
                throw new Error(result.message || 'Failed to create meeting');
            }
        } catch (error) {
            console.error('Create meeting error:', error);
            this.showToast(`Failed to create meeting: ${error.message}`, 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    showMeetingInitialControls() {
        const activeControls = document.getElementById('active-meeting-controls');
        const setupCard = document.querySelector('.meeting-setup-card');
        
        if (activeControls) {
            activeControls.style.display = 'block';
            
            document.getElementById('start-attendance').style.display = 'block';
            document.getElementById('stop-attendance').style.display = 'none';
            document.getElementById('start-recording').style.display = 'block';
            document.getElementById('stop-recording').style.display = 'none';
            document.getElementById('end-meeting').style.display = 'block';
            
            this.attendanceActive = false;
            this.audioRecordingActive = false;
            this.videoRecordingActive = false;
            
            const statusText = document.getElementById('meeting-status-text');
            if (statusText) {
                statusText.textContent = 'Meeting Active';
            }
        }
        if (setupCard) setupCard.style.display = 'none';
    }

    updateMeetingInfo() {
        if (!this.currentMeeting) return;

        const meetingTitleElement = document.getElementById('meeting-title-display');
        const meetingDateElement = document.getElementById('meeting-date-display');
        const meetingDuration = document.getElementById('meeting-duration');
        const meetingStatusElement = document.getElementById('meeting-status-text');
        const meetingStatus = document.getElementById('meeting-status');

        if (meetingTitleElement) {
            meetingTitleElement.textContent = this.currentMeeting.title || 'Meeting';
        }
        
        if (meetingDateElement) {
            const meetingDate = new Date(this.currentMeeting.start_time || Date.now())
                .toLocaleDateString('en-US', { 
                    year: 'numeric', 
                    month: 'long', 
                    day: 'numeric' 
                });
            meetingDateElement.textContent = meetingDate;
        }
        
        if (meetingDuration) meetingDuration.textContent = '00:00:00';
        if (meetingStatusElement) meetingStatusElement.textContent = 'Meeting Active';
        if (meetingStatus) {
            meetingStatus.classList.add('active');
            meetingStatus.classList.remove('ended');
        }
    }

    startMeetingTimer() {
        let startTime = Date.now();
        
        this.meetingInterval = setInterval(() => {
            const elapsed = Date.now() - startTime;
            const hours = Math.floor(elapsed / 3600000);
            const minutes = Math.floor((elapsed % 3600000) / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);
            
            const durationElement = document.getElementById('meeting-duration');
            if (durationElement) {
                durationElement.textContent = 
                    `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }
        }, 1000);
    }

    async resetMeeting() {
        if (this.isProcessing) return;
        
        this.showConfirmationModal(
            'Start New Meeting',
            'This will reset the current meeting and start fresh. Are you sure?',
            async () => {
                this.isProcessing = true;
                this.showLoading('Resetting meeting system...');

                try {
                    if (this.attendanceActive) await this.stopAttendance();
                    if (this.audioRecordingActive) await this.stopMeetingAudioRecording();
                    if (this.videoRecordingActive) await this.stopVideoRecording();
                    
                    this.disconnectWebSocket();
                    
                    this.currentMeeting = null;
                    this.lastMeetingId = null;
                    this.attendanceActive = false;
                    this.audioRecordingActive = false;
                    this.videoRecordingActive = false;
                    
                    this.cleanupMeetingState();
                    this.resetMeetingForm();
                    
                    this.showToast('Meeting system reset. Ready for new meeting.', 'success');
                } catch (error) {
                    console.error('Reset meeting error:', error);
                    this.showToast('Failed to reset meeting', 'error');
                } finally {
                    this.hideLoading();
                    this.isProcessing = false;
                }
            }
        );
    }

    resetMeetingForm() {
        const titleInput = document.getElementById('meeting-title');
        const agendaInput = document.getElementById('meeting-agenda');
        const emailInput = document.getElementById('meeting-emails');
        
        if (titleInput) titleInput.value = '';
        if (agendaInput) agendaInput.value = '';
        if (emailInput) emailInput.value = '';
        
        const activeControls = document.getElementById('active-meeting-controls');
        const setupCard = document.querySelector('.meeting-setup-card');
        
        if (activeControls) activeControls.style.display = 'none';
        if (setupCard) setupCard.style.display = 'block';
        
        const canvas = document.getElementById('video-canvas');
        const overlay = document.getElementById('video-overlay');
        
        if (canvas) {
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
        if (overlay) overlay.style.display = 'flex';
    }

    async endMeeting() {
        if (this.isProcessing) return;
        
        if (!this.currentMeeting) {
            this.showToast('No active meeting to end', 'error');
            return;
        }

        this.showConfirmationModal(
            'End Meeting',
            'Are you sure you want to end this meeting? All recordings will be processed and summary will be generated.',
            async () => {
                this.isProcessing = true;
                this.showLoading('Ending meeting and processing recordings...');
                
                try {
                    if (this.attendanceActive) {
                        await this.stopAttendance();
                    }
                    
                    if (this.audioRecordingActive || this.videoRecordingActive) {
                        await this.stopMeetingAudioRecording();
                    }
                    
                    this.disconnectWebSocket();
                    
                    const response = await fetch('/end_meeting', {
                        method: 'POST'
                    });

                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }
                    
                    const result = await response.json();
                    
                    if (result.status === 'success') {
                        this.showProcessingOverlay('Processing meeting data...');
                        
                        await new Promise(resolve => setTimeout(resolve, 3000));
                        
                        this.hideProcessingOverlay();
                        
                        this.showSummaryPopup(result.meeting);
                        
                        this.showToast('Meeting ended successfully', 'success');
                        
                        this.cleanupMeetingState();
                    } else {
                        throw new Error(result.message || 'Failed to end meeting');
                    }
                    
                } catch (error) {
                    console.error('End meeting error:', error);
                    this.showToast(`Failed to end meeting: ${error.message}`, 'error');
                    this.cleanupMeetingState();
                    
                } finally {
                    this.hideLoading();
                    this.isProcessing = false;
                }
            }
        );
    }

    showProcessingOverlay(message) {
        const overlay = document.getElementById('processing-overlay');
        if (!overlay) {
            const overlayDiv = document.createElement('div');
            overlayDiv.id = 'processing-overlay';
            overlayDiv.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(15, 23, 42, 0.95);
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                z-index: 9999;
                color: white;
                font-size: 1.2em;
            `;
            overlayDiv.innerHTML = `
                <div class="spinner" style="width: 60px; height: 60px; border: 4px solid rgba(59, 130, 246, 0.2); border-top: 4px solid var(--primary); border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 20px;"></div>
                <p>${message}</p>
                <p style="font-size: 0.8em; margin-top: 10px; color: var(--text-muted);">Please wait while we process your meeting...</p>
            `;
            document.body.appendChild(overlayDiv);
        } else {
            overlay.style.display = 'flex';
        }
    }

    hideProcessingOverlay() {
        const overlay = document.getElementById('processing-overlay');
        if (overlay) {
            overlay.style.display = 'none';
        }
    }

    showConfirmationModal(title, message, confirmCallback) {
        let modal = document.getElementById('confirmation-modal');
        
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'confirmation-modal';
            modal.className = 'modal';
            modal.innerHTML = `
                <div class="modal-content">
                    <div class="modal-header">
                        <h3 id="modal-title">${title}</h3>
                        <button class="modal-close" id="modal-close">&times;</button>
                    </div>
                    <div class="modal-body">
                        <p id="modal-message">${message}</p>
                    </div>
                    <div class="modal-footer">
                        <button id="modal-cancel" class="btn btn-secondary">Cancel</button>
                        <button id="modal-confirm" class="btn btn-error">Confirm</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        } else {
            document.getElementById('modal-title').textContent = title;
            document.getElementById('modal-message').textContent = message;
        }
        
        const oldConfirm = document.getElementById('modal-confirm');
        const newConfirm = oldConfirm.cloneNode(true);
        oldConfirm.parentNode.replaceChild(newConfirm, oldConfirm);
        
        const oldClose = document.getElementById('modal-close');
        const newClose = oldClose.cloneNode(true);
        oldClose.parentNode.replaceChild(newClose, oldClose);
        
        const oldCancel = document.getElementById('modal-cancel');
        const newCancel = oldCancel.cloneNode(true);
        oldCancel.parentNode.replaceChild(newCancel, oldCancel);
        
        document.getElementById('modal-close').addEventListener('click', () => this.hideModal());
        document.getElementById('modal-cancel').addEventListener('click', () => this.hideModal());
        document.getElementById('modal-confirm').addEventListener('click', () => {
            if (confirmCallback) {
                confirmCallback();
            }
            this.hideModal();
        });
        
        this.confirmCallback = confirmCallback;
        modal.style.display = 'flex';
        setTimeout(() => modal.classList.add('show'), 10);
    }

    hideModal() {
        const modal = document.getElementById('confirmation-modal');
        if (modal) {
            modal.classList.remove('show');
            setTimeout(() => modal.style.display = 'none', 300);
        }
        this.confirmCallback = null;
    }

    async showSummaryPopup(meeting) {
        const popup = document.getElementById('summary-popup');
        if (!popup) return;
        
        this.lastMeetingId = meeting.id;
        
        const folderName = meeting.folder || meeting.id;
        
        let displayTitle = meeting.title || 'Meeting';
        displayTitle = displayTitle.replace(/\.(wav|mp4|mp3|pdf|json|xlsx)$/i, '');
        displayTitle = displayTitle.replace(/meeting_/gi, '');
        displayTitle = displayTitle.replace(/^\d+_/, '');
        displayTitle = displayTitle.split('_').join(' ');
        displayTitle = displayTitle.charAt(0).toUpperCase() + displayTitle.slice(1);
        
        const title = document.getElementById('summary-meeting-title');
        const date = document.getElementById('summary-meeting-date');
        const id = document.getElementById('summary-meeting-id');
        const duration = document.getElementById('summary-meeting-duration');
        
        if (title) title.textContent = displayTitle;
        if (date) {
            const meetingDate = meeting.date || new Date().toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'long', 
                day: 'numeric' 
            });
            date.textContent = meetingDate;
        }
        if (id) id.textContent = `ID: ${meeting.id || 'N/A'}`;
        if (duration) duration.textContent = meeting.duration || '00:00:00';
        
        popup.style.display = 'flex';
        setTimeout(() => popup.classList.add('show'), 10);
        
        await this.checkAllFileStatuses(folderName);
    }

    async checkAllFileStatuses(folderName) {
        if (!this.lastMeetingId || !folderName) return;
        
        const fileTypes = [
            { id: 'summary-pdf-status', type: 'summary', name: 'Summary PDF' },
            { id: 'summary-excel-status', type: 'attendance', name: 'Attendance Excel' },
            { id: 'summary-transcript-status', type: 'transcript', name: 'Transcript PDF' },
            { id: 'summary-audio-status', type: 'audio', name: 'Audio Recording' },
            { id: 'summary-video-status', type: 'video', name: 'Video Recording' },
        ];
        
        for (let i = 0; i < fileTypes.length; i++) {
            const { id, type, name } = fileTypes[i];
            
            const exists = await this.checkFileExists(type) || 
                          await this.checkFileExistsWithFolder(type, folderName);
            
            this.updateFileStatus(id, exists, name);
            
            if (i < fileTypes.length - 1) {
                await new Promise(resolve => setTimeout(resolve, 300));
            }
        }
    }

    // UPDATED: Check file exists with folder - fix video file pattern
    async checkFileExistsWithFolder(fileType, folderName) {
        try {
            let filename;
            if (fileType === 'attendance') {
                filename = `${folderName}_attendance.xlsx`;
            } else if (fileType === 'audio') {
                filename = `${folderName}_audio.wav`;
            } else if (fileType === 'summary') {
                filename = `${folderName}_summary.pdf`;
            } else if (fileType === 'transcript') {
                filename = `${folderName}_transcript.pdf`;
            } else if (fileType === 'video') {
                // FIX 3: Check video file with multiple attempts
                const response = await fetch(`/check_video_file?folder=${encodeURIComponent(folderName)}`);
                if (response.ok) {
                    const result = await response.json();
                    if (result.exists) {
                        return true;
                    }
                    
                    // If not found, wait and retry
                    await new Promise(resolve => setTimeout(resolve, 2000));
                    
                    const retryResponse = await fetch(`/check_video_file?folder=${encodeURIComponent(folderName)}`);
                    if (retryResponse.ok) {
                        const retryResult = await retryResponse.json();
                        return retryResult.exists;
                    }
                }
                return false;
            } else {
                return false;
            }
            
            const response = await fetch(`/check_file_exists?folder=${encodeURIComponent(folderName)}&file=${encodeURIComponent(filename)}`, {
                method: 'GET',
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            });
            
            return response.ok && response.status === 200;
        } catch (error) {
            console.error(`Failed to check file with folder: ${error}`);
            return false;
        }
    }

    async checkFileExists(fileType) {
        try {
            const response = await fetch(`/download_file/${this.lastMeetingId}/${fileType}`, {
                method: 'HEAD',
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            });
            
            return response.ok;
        } catch (error) {
            console.error(`Failed to check ${fileType}:`, error);
            return false;
        }
    }

    updateFileStatus(elementId, exists, fileName) {
        const element = document.getElementById(elementId);
        if (!element) return;
        
        if (exists) {
            element.textContent = 'Available ✓';
            element.className = 'summary-status-value success';
            
            // Enable download buttons based on file type
            if (elementId === 'summary-pdf-status') {
                const pdfBtn = document.getElementById('summary-download-pdf');
                if (pdfBtn) pdfBtn.disabled = false;
            } else if (elementId === 'summary-excel-status') {
                const excelBtn = document.getElementById('summary-download-excel');
                if (excelBtn) excelBtn.disabled = false;
            } else if (elementId === 'summary-transcript-status') {
                const transcriptBtn = document.getElementById('summary-download-transcript');
                if (transcriptBtn) transcriptBtn.disabled = false;
            } else if (elementId === 'summary-audio-status') {
                const audioBtn = document.getElementById('summary-download-audio');
                if (audioBtn) audioBtn.disabled = false;
            } else if (elementId === 'summary-video-status') {
                const videoBtn = document.getElementById('summary-download-video');
                if (videoBtn) videoBtn.disabled = false;
            }
        } else {
            element.textContent = 'Not Available';
            element.className = 'summary-status-value error';
            
            // Disable download buttons
            if (elementId === 'summary-pdf-status') {
                const pdfBtn = document.getElementById('summary-download-pdf');
                if (pdfBtn) {
                    pdfBtn.disabled = true;
                    pdfBtn.title = 'PDF not available yet';
                }
            } else if (elementId === 'summary-excel-status') {
                const excelBtn = document.getElementById('summary-download-excel');
                if (excelBtn) {
                    excelBtn.disabled = true;
                    excelBtn.title = 'Attendance Excel not available yet';
                }
            } else if (elementId === 'summary-transcript-status') {
                const transcriptBtn = document.getElementById('summary-download-transcript');
                if (transcriptBtn) {
                    transcriptBtn.disabled = true;
                    transcriptBtn.title = 'Transcript PDF not available yet';
                }
            } else if (elementId === 'summary-audio-status') {
                const audioBtn = document.getElementById('summary-download-audio');
                if (audioBtn) {
                    audioBtn.disabled = true;
                    audioBtn.title = 'Audio recording not available yet';
                }
            } else if (elementId === 'summary-video-status') {
                const videoBtn = document.getElementById('summary-download-video');
                if (videoBtn) {
                    videoBtn.disabled = true;
                    videoBtn.title = 'Video recording not available yet';
                }
            }
        }
    }

    async safeDownload(meetingId, fileType, fileName) {
        if (this.isProcessing) {
            this.showToast('Another download is in progress', 'error');
            return false;
        }
        
        this.isProcessing = true;
        this.showLoading(`Downloading ${fileName}...`);

        try {
            const response = await fetch(`/download_file/${meetingId}/${fileType}`);
            
            if (response.ok) {
                const blob = await response.blob();
                
                if (blob.size === 0) {
                    this.showToast(`${fileName} is not available yet. Please wait a moment.`, 'error');
                    return false;
                }
                
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                
                let actualFileName = fileName;
                const contentDisposition = response.headers.get('Content-Disposition');
                if (contentDisposition) {
                    const match = contentDisposition.match(/filename="(.+?)"/);
                    if (match && match[1]) {
                        actualFileName = match[1];
                    }
                }
                
                a.href = url;
                a.download = actualFileName;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
                
                this.showToast(`${actualFileName} downloaded successfully`, 'success');
                return true;
            } else {
                if (response.status === 404) {
                    this.showToast(`${fileName} is still being processed. Please try again in a moment.`, 'warning');
                } else {
                    this.showToast(`Failed to download ${fileName}: Server error ${response.status}`, 'error');
                }
                return false;
            }
        } catch (error) {
            console.error(`Download ${fileType} error:`, error);
            this.showToast(`Failed to download ${fileName}: ${error.message}`, 'error');
            return false;
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    hideSummaryPopup() {
        const popup = document.getElementById('summary-popup');
        if (popup) {
            popup.classList.remove('show');
            setTimeout(() => popup.style.display = 'none', 300);
        }
    }

    async downloadVideo() {
        if (!this.lastMeetingId) {
            this.showToast('No meeting video available', 'error');
            return;
        }
        await this.safeDownload(this.lastMeetingId, 'video', 'meeting_video.mp4');
    }

    async downloadTranscript() {
        if (!this.lastMeetingId) {
            this.showToast('No meeting transcript available', 'error');
            return;
        }
        await this.safeDownload(this.lastMeetingId, 'transcript', 'meeting_transcript.pdf');
    }

    async downloadAudio() {
        if (!this.lastMeetingId) {
            this.showToast('No meeting audio available', 'error');
            return;
        }
        await this.safeDownload(this.lastMeetingId, 'audio', 'meeting_audio.wav');
    }

    async downloadSummary() {
        if (!this.lastMeetingId) {
            this.showToast('No meeting summary available', 'error');
            return;
        }
        await this.safeDownload(this.lastMeetingId, 'summary', 'meeting_summary.pdf');
    }

    async downloadAttendance() {
        if (!this.lastMeetingId) {
            this.showToast('No meeting attendance available', 'error');
            return;
        }
        await this.safeDownload(this.lastMeetingId, 'attendance', 'meeting_attendance.xlsx');
    }

    // MODIFIED: Cleanup meeting state with video recording cleanup
    cleanupMeetingState() {
        this.currentMeeting = null;
        this.transcripts = [];
        
        this.attendanceActive = false;
        this.audioRecordingActive = false;
        this.videoRecordingActive = false;
        
        if (this.meetingInterval) {
            clearInterval(this.meetingInterval);
            this.meetingInterval = null;
        }
        
        this.stopAudioTimer();
        
        localStorage.removeItem('currentMeeting');
        localStorage.removeItem('lastMeetingId');
        
        const meetingStatusElement = document.getElementById('meeting-status-text');
        const meetingStatus = document.getElementById('meeting-status');
        const meetingDuration = document.getElementById('meeting-duration');
        
        if (meetingStatusElement) meetingStatusElement.textContent = 'No Active Meeting';
        if (meetingStatus) {
            meetingStatus.classList.remove('active');
            meetingStatus.classList.remove('ended');
        }
        if (meetingDuration) meetingDuration.textContent = '00:00:00';
        
        const activeControls = document.getElementById('active-meeting-controls');
        const setupCard = document.querySelector('.meeting-setup-card');
        
        if (activeControls) {
            activeControls.style.display = 'none';
        }
        if (setupCard) setupCard.style.display = 'block';
        
        this.toggleAttendanceButtons(false);
        this.toggleRecordingButtons(false);
        this.disconnectWebSocket();
        
        // Added video recording cleanup
        this.stopVideoRecordingTimer();
        this.updateVideoRecordingUI(false);
    }

    async emailSummary() {
        if (this.isProcessing) return;
        
        if (!this.lastMeetingId) {
            this.showToast('No meeting summary available', 'error');
            return;
        }

        this.isProcessing = true;
        this.showLoading('Sending summary email...');

        try {
            const formData = new FormData();
            formData.append('meeting_id', this.lastMeetingId);
            
            const emailInput = document.getElementById('meeting-emails');
            if (emailInput && emailInput.value.trim()) {
                formData.append('recipients', emailInput.value.trim());
            }

            const response = await fetch('/api/send_meeting_email', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();
            if (result.status === 'success') {
                this.showToast(result.message || 'Summary emailed successfully', 'success');
            } else {
                this.showToast(result.message || 'Failed to send summary email', 'error');
            }
        } catch (error) {
            console.error('Email summary error:', error);
            this.showToast('Failed to send email. Please check configuration.', 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    checkAudioSupport() {
        const hasAudioContext = !!(window.AudioContext || window.webkitAudioContext);
        const hasGetUserMedia = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
        
        if (!hasAudioContext) {
            console.error('Web Audio API not supported');
            this.showToast('Your browser does not support audio recording. Please use Chrome, Firefox, or Edge.', 'error');
            return false;
        }
        
        if (!hasGetUserMedia) {
            console.error('getUserMedia not supported');
            this.showToast('Microphone access not available in your browser.', 'error');
            return false;
        }
        
        return true;
    }

    async startAudioCapture() {
        try {
            if (!this.checkAudioSupport()) {
                return;
            }

            this.audioRecordingChunks = [];
            this.audioRecordingStartTime = null;
            
            const stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                    latency: 0,
                    sampleSize: 16
                },
                video: false
            });
            
            const audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 16000
            });
            
            const source = audioContext.createMediaStreamSource(stream);
            const processor = audioContext.createScriptProcessor(4096, 1, 1);
            
            this.audioRecordingChunks = [];
            
            processor.onaudioprocess = (event) => {
                const inputData = event.inputBuffer.getChannelData(0);
                const buffer = new Float32Array(inputData.length);
                
                for (let i = 0; i < inputData.length; i++) {
                    buffer[i] = inputData[i];
                }
                
                const pcmData = this.floatTo16BitPCM(buffer);
                this.audioRecordingChunks.push(pcmData);
            };
            
            source.connect(processor);
            processor.connect(audioContext.destination);
            
            this.audioStream = stream;
            this.audioContext = audioContext;
            this.audioProcessor = processor;
            this.audioSource = source;
            
            this.audioRecordingStartTime = Date.now();
            
            this.showAudioRecordingControls();
            
            this.startAudioRecordingTimer();
            
            this.showToast('Recording started. Speak normally...', 'info');
            
        } catch (error) {
            console.error('Audio capture error:', error);
            if (error.name === 'NotAllowedError') {
                this.showToast('Microphone access denied. Please allow microphone access.', 'error');
            } else if (error.name === 'NotFoundError') {
                this.showToast('No microphone found. Please connect a microphone.', 'error');
            } else {
                this.showToast('Failed to access microphone: ' + error.message, 'error');
            }
        }
    }

    floatTo16BitPCM(input) {
        const output = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            output[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return output;
    }

    stopAudioRecording() {
        if (!this.audioContext || !this.audioProcessor) {
            this.showToast('No active recording found', 'error');
            return;
        }
        
        try {
            this.audioProcessor.disconnect();
            this.audioSource.disconnect();
            this.audioProcessor.onaudioprocess = null;
            
            this.audioContext.close();
            
            if (this.audioStream) {
                this.audioStream.getTracks().forEach(track => track.stop());
            }
            
            if (this.audioRecordingChunks.length > 0) {
                const totalLength = this.audioRecordingChunks.reduce((sum, chunk) => sum + chunk.length, 0);
                const audioData = new Int16Array(totalLength);
                let offset = 0;
                
                for (const chunk of this.audioRecordingChunks) {
                    audioData.set(chunk, offset);
                    offset += chunk.length;
                }
                
                const wavBlob = this.createWavBlob(audioData, 16000);
                
                this.previewAudioFile(wavBlob);
                
                this.showToast('Audio recording saved successfully!', 'success');
            } else {
                this.showToast('No audio data recorded', 'warning');
            }
            
            this.hideAudioRecordingControls();
            
            this.audioStream = null;
            this.audioContext = null;
            this.audioProcessor = null;
            this.audioSource = null;
            this.audioRecordingChunks = [];
            
        } catch (error) {
            console.error('Stop audio recording error:', error);
            this.showToast('Failed to stop recording: ' + error.message, 'error');
        }
    }

    createWavBlob(pcmData, sampleRate) {
        const buffer = new ArrayBuffer(44 + pcmData.length * 2);
        const view = new DataView(buffer);
        
        this.writeString(view, 0, 'RIFF');
        view.setUint32(4, 36 + pcmData.length * 2, true);
        this.writeString(view, 8, 'WAVE');
        this.writeString(view, 12, 'fmt ');
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, 1, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, sampleRate * 2, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        this.writeString(view, 36, 'data');
        view.setUint32(40, pcmData.length * 2, true);
        
        const offset = 44;
        for (let i = 0; i < pcmData.length; i++) {
            view.setInt16(offset + (i * 2), pcmData[i], true);
        }
        
        return new Blob([buffer], { type: 'audio/wav' });
    }

    writeString(view, offset, string) {
        for (let i = 0; i < string.length; i++) {
            view.setUint8(offset + i, string.charCodeAt(i));
        }
    }

    cancelAudioRecording() {
        if (this.audioContext) {
            this.audioContext.close();
        }
        
        if (this.audioStream) {
            this.audioStream.getTracks().forEach(track => track.stop());
        }
        
        this.audioRecordingChunks = [];
        this.audioStream = null;
        this.audioContext = null;
        this.audioProcessor = null;
        this.audioSource = null;
        
        this.hideAudioRecordingControls();
        this.showToast('Audio recording cancelled', 'info');
    }

    uploadAudioFile() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'audio/*,.wav,.mp3,.m4a,.ogg';
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (file) {
                if (file.size > 10 * 1024 * 1024) {
                    this.showToast('Audio file too large (max 10MB)', 'error');
                    return;
                }
                
                const validTypes = ['audio/wav', 'audio/mpeg', 'audio/mp3', 'audio/mp4', 'audio/ogg', 'audio/webm'];
                if (!validTypes.includes(file.type) && !file.name.match(/\.(wav|mp3|m4a|ogg|webm)$/i)) {
                    this.showToast('Invalid audio file format', 'error');
                    return;
                }
                
                try {
                    let wavBlob;
                    if (file.type === 'audio/wav' || file.name.endsWith('.wav')) {
                        wavBlob = file;
                    } else {
                        wavBlob = await this.convertAudioToWav(file);
                    }
                    
                    const audioInput = document.getElementById('attendee-audio');
                    if (audioInput) {
                        const audioFile = new File([wavBlob], `${file.name.replace(/\.[^/.]+$/, "")}.wav`, { 
                            type: 'audio/wav' 
                        });
                        const dataTransfer = new DataTransfer();
                        dataTransfer.items.add(audioFile);
                        audioInput.files = dataTransfer.files;
                        
                        this.previewAudioFile(wavBlob);
                    }
                } catch (error) {
                    console.error('Audio upload error:', error);
                    this.showToast('Failed to process audio file', 'error');
                }
            }
        };
        input.click();
    }

    async convertAudioToWav(file) {
        return new Promise((resolve, reject) => {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 16000
            });
            
            const reader = new FileReader();
            reader.onload = async (e) => {
                try {
                    const audioData = e.target.result;
                    const audioBuffer = await audioContext.decodeAudioData(audioData);
                    
                    const offlineContext = new OfflineAudioContext(
                        1,
                        audioBuffer.duration * 16000,
                        16000
                    );
                    
                    const source = offlineContext.createBufferSource();
                    source.buffer = audioBuffer;
                    source.connect(offlineContext.destination);
                    source.start(0);
                    
                    const renderedBuffer = await offlineContext.startRendering();
                    
                    const wavBlob = this.audioBufferToWav(renderedBuffer);
                    resolve(wavBlob);
                    
                } catch (error) {
                    reject(error);
                }
            };
            
            reader.onerror = () => reject(new Error('Failed to read file'));
            reader.readAsArrayBuffer(file);
        });
    }

    audioBufferToWav(buffer) {
        const numChannels = buffer.numberOfChannels;
        const sampleRate = buffer.sampleRate;
        const length = buffer.length * numChannels * 2 + 44;
        const arrayBuffer = new ArrayBuffer(length);
        const view = new DataView(arrayBuffer);
        
        this.writeString(view, 0, 'RIFF');
        view.setUint32(4, length - 8, true);
        this.writeString(view, 8, 'WAVE');
        this.writeString(view, 12, 'fmt ');
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, numChannels, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, sampleRate * numChannels * 2, true);
        view.setUint16(32, numChannels * 2, true);
        view.setUint16(34, 16, true);
        this.writeString(view, 36, 'data');
        view.setUint32(40, buffer.length * numChannels * 2, true);
        
        let offset = 44;
        for (let i = 0; i < buffer.length; i++) {
            for (let channel = 0; channel < numChannels; channel++) {
                const sample = Math.max(-1, Math.min(1, buffer.getChannelData(channel)[i]));
                view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7FFF, true);
                offset += 2;
            }
        }
        
        return new Blob([arrayBuffer], { type: 'audio/wav' });
    }

    previewAudioFile(file) {
        const audioPreview = document.getElementById('audio-preview');
        const audioPlayer = document.getElementById('audio-player');
        
        if (!audioPreview || !audioPlayer) return;
        
        const audioURL = URL.createObjectURL(file);
        audioPlayer.src = audioURL;
        
        audioPreview.style.display = 'block';
        setTimeout(() => audioPreview.classList.add('show'), 10);
        
        const audioInput = document.getElementById('attendee-audio');
        if (audioInput) {
            const attendeeName = document.getElementById('attendee-name').value.trim();
            const fileName = attendeeName ? `${attendeeName.replace(/\s+/g, '_')}.wav` : `audio_sample_${Date.now()}.wav`;
            
            const audioFile = new File([file], fileName, { type: 'audio/wav' });
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(audioFile);
            audioInput.files = dataTransfer.files;
        }
        
        setTimeout(() => {
            audioPlayer.currentTime = 0;
            audioPlayer.volume = 0.5;
            audioPlayer.play().catch(() => {
            });
        }, 500);
    }

    removeAudioPreview() {
        const audioPreview = document.getElementById('audio-preview');
        const audioPlayer = document.getElementById('audio-player');
        const audioInput = document.getElementById('attendee-audio');
        
        if (audioPlayer && audioPlayer.src) {
            URL.revokeObjectURL(audioPlayer.src);
            audioPlayer.src = '';
        }
        
        if (audioPreview) {
            audioPreview.classList.remove('show');
            setTimeout(() => audioPreview.style.display = 'none', 300);
        }
        
        if (audioInput) {
            audioInput.value = '';
        }
        
        if (this.audioContext && this.audioContext.state === 'running') {
            this.cancelAudioRecording();
        }
    }

    showAudioRecordingControls() {
        const audioControls = document.getElementById('audio-recording-controls');
        const visualizer = document.getElementById('audio-visualizer');
        
        if (audioControls) {
            audioControls.style.display = 'block';
            setTimeout(() => audioControls.classList.add('show'), 10);
        }
        
        if (visualizer) {
            visualizer.style.display = 'flex';
        }
        
        const timer = document.getElementById('audio-recording-timer');
        if (timer) timer.textContent = '00:00';
    }

    hideAudioRecordingControls() {
        const audioControls = document.getElementById('audio-recording-controls');
        const visualizer = document.getElementById('audio-visualizer');
        
        if (audioControls) {
            audioControls.classList.remove('show');
            setTimeout(() => audioControls.style.display = 'none', 300);
        }
        
        if (visualizer) {
            visualizer.style.display = 'none';
        }
        
        this.stopAudioRecordingTimer();
    }

    startAudioRecordingTimer() {
        if (this.audioRecordingTimer) {
            clearInterval(this.audioRecordingTimer);
        }
        
        this.audioRecordingStartTime = Date.now();
        this.audioRecordingTimer = setInterval(() => {
            const elapsed = Date.now() - this.audioRecordingStartTime;
            const totalSeconds = Math.floor(elapsed / 1000);
            const minutes = Math.floor(totalSeconds / 60);
            const seconds = totalSeconds % 60;
            
            const timer = document.getElementById('audio-recording-timer');
            if (timer) {
                timer.textContent = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }
        }, 1000);
    }

    stopAudioRecordingTimer() {
        if (this.audioRecordingTimer) {
            clearInterval(this.audioRecordingTimer);
            this.audioRecordingTimer = null;
        }
        this.audioRecordingStartTime = null;
    }

    async addAttendee(e) {
        e.preventDefault();
        
        if (this.isProcessing) return;
        
        const nameInput = document.getElementById('attendee-name');
        const photoInput = document.getElementById('attendee-photo');
        const audioInput = document.getElementById('attendee-audio');
        
        if (!nameInput) {
            this.showToast('Registration form elements not found', 'error');
            return;
        }

        const name = nameInput.value.trim();
        const photo = photoInput ? photoInput.files[0] : null;
        const audio = audioInput ? audioInput.files[0] : null;

        if (!name) {
            this.showToast('Please enter attendee name', 'error');
            nameInput.focus();
            return;
        }

        if (!photo && !audio) {
            this.showToast('Please provide either a photo or audio sample', 'error');
            return;
        }

        this.isProcessing = true;
        this.showLoading('Registering attendee...');

        try {
            const formData = new FormData();
            formData.append('name', name);
            
            if (photo) {
                formData.append('photo', photo);
            }
            
            if (audio) {
                formData.append('audio', audio);
            }

            const response = await fetch('/add_attendee', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (result.status === 'success') {
                this.showToast(result.message || `Attendee "${name}" registered successfully`, 'success');
                this.resetAttendeeForm();
                this.refreshAttendance();
            } else {
                this.showToast(result.message || 'Failed to register attendee', 'error');
            }
        } catch (error) {
            console.error('Add attendee error:', error);
            this.showToast('Failed to register attendee: ' + error.message, 'error');
        } finally {
            this.hideLoading();
            this.isProcessing = false;
        }
    }

    previewImage(e) {
        const file = e.target.files[0];
        if (file) {
            const validTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp'];
            if (!validTypes.includes(file.type)) {
                this.showToast('Please select a valid image file (JPG, PNG, WebP)', 'error');
                e.target.value = '';
                return;
            }

            if (file.size > 5 * 1024 * 1024) {
                this.showToast('Image file size should be less than 5MB', 'error');
                e.target.value = '';
                return;
            }

            const reader = new FileReader();
            reader.onload = (e) => {
                const previewImage = document.getElementById('preview-image');
                const filePreview = document.getElementById('file-preview');
                
                if (previewImage) previewImage.src = e.target.result;
                if (filePreview) {
                    filePreview.style.display = 'block';
                    setTimeout(() => filePreview.classList.add('show'), 10);
                }
            };
            reader.readAsDataURL(file);
        }
    }

    removeImagePreview() {
        const previewImage = document.getElementById('preview-image');
        const filePreview = document.getElementById('file-preview');
        const photoInput = document.getElementById('attendee-photo');
        
        if (previewImage) previewImage.src = '';
        if (filePreview) {
            filePreview.classList.remove('show');
            setTimeout(() => filePreview.style.display = 'none', 300);
        }
        if (photoInput) photoInput.value = '';
    }

    resetAttendeeForm() {
        const form = document.getElementById('add-attendee-form');
        if (form) form.reset();
        this.removeImagePreview();
        this.removeAudioPreview();
    }

    async showSystemStatus() {
        if (this.isProcessing) return;
        
        this.isProcessing = true;

        try {
            const response = await fetch('/system-status');
            const result = await response.json();

            let statusMessage = `System Status: ${result.status || 'Unknown'}\n`;
            statusMessage += `Camera Active: ${result.camera_active ? 'Yes' : 'No'}\n`;
            statusMessage += `Known Persons: ${result.known_persons || 0}\n`;
            statusMessage += `Meeting Active: ${result.meeting_active ? 'Yes' : 'No'}\n`;
            statusMessage += `Attendance Tracking: ${result.attendance_active ? 'Active' : 'Inactive'}\n`;
            statusMessage += `Audio Recording: ${result.audio_recording_active ? 'Active' : 'Inactive'}\n`;
            statusMessage += `Video Recording: ${result.video_recording_active ? 'Active' : 'Inactive'}\n`;
            
            if (result.obs_connected) {
                statusMessage += `OBS Connected: Yes\n`;
            }

            alert(statusMessage);
        } catch (error) {
            console.error('System status error:', error);
            this.showToast('Failed to get system status', 'error');
        } finally {
            this.isProcessing = false;
        }
    }

    showLoading(message = 'Processing...') {
        const overlay = document.getElementById('loading-overlay');
        const spinner = overlay?.querySelector('p');
        
        if (overlay) {
            overlay.style.display = 'flex';
            setTimeout(() => overlay.classList.add('show'), 10);
            if (spinner) spinner.textContent = message;
        }
    }

    hideLoading() {
        const overlay = document.getElementById('loading-overlay');
        if (overlay) {
            overlay.classList.remove('show');
            setTimeout(() => {
                overlay.style.display = 'none';
                const spinner = overlay.querySelector('p');
                if (spinner) spinner.textContent = 'Processing...';
            }, 300);
        }
    }

    showToast(message, type = 'info') {
        const toast = document.getElementById('status-toast');
        const toastIcon = toast?.querySelector('.toast-icon');
        const toastMessage = toast?.querySelector('.toast-message');
        
        if (!toast || !toastIcon || !toastMessage) return;
        
        if (this.toastTimeout) {
            clearTimeout(this.toastTimeout);
        }
        
        toast.className = `toast toast-${type}`;
        toastMessage.textContent = message;
        
        const icons = {
            success: 'fas fa-check-circle',
            error: 'fas fa-exclamation-circle',
            warning: 'fas fa-exclamation-triangle',
            info: 'fas fa-info-circle'
        };
        
        toastIcon.className = `toast-icon ${icons[type] || icons.info}`;
        toast.style.display = 'flex';
        setTimeout(() => toast.classList.add('show'), 10);
        
        this.toastTimeout = setTimeout(() => this.hideToast(), 5000);
    }

    hideToast() {
        const toast = document.getElementById('status-toast');
        if (toast) {
            toast.classList.remove('show');
            setTimeout(() => toast.style.display = 'none', 300);
        }
    }

    showDiarizationProgress(message) {
        this.showToast(message, 'info');
        if (this.toastTimeout) clearTimeout(this.toastTimeout);
        this.toastTimeout = setTimeout(() => this.hideToast(), 10000);
    }

    confirmAction() {
        if (this.confirmCallback) {
            this.confirmCallback();
        }
        this.hideModal();
    }

    showErrorPage(error) {
        const errorMsg = document.createElement('div');
        errorMsg.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #ef4444;
            color: white;
            padding: 2rem;
            border-radius: 0.75rem;
            z-index: 10000;
            text-align: center;
            max-width: 400px;
            width: 90%;
        `;
        errorMsg.innerHTML = `
            <h3 style="margin-bottom: 1rem;">Application Error</h3>
            <p style="margin-bottom: 1.5rem;">Failed to initialize the meeting manager. Please refresh the page.</p>
            <p style="font-size: 0.8em; opacity: 0.8; margin-bottom: 1rem;">Error: ${error.message}</p>
            <button onclick="location.reload()" style="padding: 0.75rem 1.5rem; background: white; color: #ef4444; border: none; border-radius: 0.5rem; cursor: pointer; font-weight: 600;">Refresh Page</button>
        `;
        document.body.appendChild(errorMsg);
    }

    // MODIFIED: Update loadSystemStatus to check OBS status
    async loadSystemStatus() {
        try {
            const response = await fetch('/system-status');
            const result = await response.json();
            
            this.initializeAttendanceSystem();
            
            if (result.meeting_active && result.meeting) {
                this.currentMeeting = result.meeting;
                this.lastMeetingId = result.meeting.id;
                
                if (typeof this.currentMeeting.start_time === 'string') {
                    this.currentMeeting.start_time = new Date(this.currentMeeting.start_time);
                }
                
                this.showMeetingInitialControls();
                this.updateMeetingInfo();
                this.startMeetingTimer();
                
                this.attendanceActive = result.attendance_active || false;
                this.audioRecordingActive = result.audio_recording_active || false;
                this.videoRecordingActive = result.video_recording_active || false;
                
                if (result.attendance_active) {
                    this.toggleAttendanceButtons(true);
                    this.connectAttendanceWebSocket();
                }
                
                if (result.audio_recording_active || result.video_recording_active) {
                    this.toggleRecordingButtons(true);
                }
                
                if (result.video_recording_active) {
                    this.updateVideoRecordingUI(true);
                    this.startVideoRecordingTimer();
                }
                
                // Check OBS status
                if (result.obs_connected) {
                    console.log('✅ OBS is connected');
                    this.showToast('OBS is connected and ready', 'success');
                }
                
                this.showToast('Meeting state restored', 'info');
            }
        } catch (error) {
            console.log('Could not restore meeting state:', error);
        }
    }

    initializeAttendanceSystem() {
        try {
            console.log("Initializing attendance system...");
            this.refreshAttendance();
            
            setInterval(() => {
                if (this.currentMeeting) {
                    this.refreshAttendance();
                }
            }, 10000);
            
        } catch (error) {
            console.error('Failed to initialize attendance system:', error);
        }
    }

    stopAudioTimer() {
        if (this.audioRecordingTimer) {
            clearInterval(this.audioRecordingTimer);
            this.audioRecordingTimer = null;
        }
    }

    // Attendance timer methods
    startAttendanceTimer() {
        // Timer for attendance duration if needed
    }

    stopAttendanceTimer() {
        // Stop attendance timer if needed
    }
}

// Initialize SmartMeetingManager
window.smartMeetingManager = new SmartMeetingManager();

// Only connect to attendance WebSocket on page load
document.addEventListener('DOMContentLoaded', () => {
    // Connect to attendance WebSocket
    smartMeetingManager.connectAttendanceWebSocket();
});

document.addEventListener('visibilitychange', () => {
    if (!document.hidden && window.smartMeetingManager) {
        window.smartMeetingManager.refreshAttendance();
    }
});

window.addEventListener('beforeunload', () => {
    if (window.smartMeetingManager) {
        if (window.smartMeetingManager.mediaRecorder && window.smartMeetingManager.mediaRecorder.state === 'recording') {
            window.smartMeetingManager.stopAudioRecording();
        }
        
        if (window.smartMeetingManager.attendanceWebSocket) {
            window.smartMeetingManager.attendanceWebSocket.close();
        }
        
        if (window.smartMeetingManager.liveFeedWebSocket) {
            window.smartMeetingManager.liveFeedWebSocket.close();
        }
        
        if (window.smartMeetingManager.attendanceWebSocketHeartbeat) {
            clearInterval(window.smartMeetingManager.attendanceWebSocketHeartbeat);
        }
    }
});