# OBS Studio Setup for MeetingSense

## Prerequisites
1. Install OBS Studio 32.0.2+
2. Install OBS WebSocket plugin: https://github.com/obsproject/obs-websocket/releases

## Configuration Steps

### 1. WebSocket Setup:
- Tools → WebSocket Server Settings
- Enable WebSocket server
- Port: `4455`
- Authentication: Disabled (for testing)
- Save & restart OBS

### 2. Recording Settings (CRITICAL):
- Settings → Output → Recording
- Recording Format: `mp4`
- Video Bitrate: `8000 Kbps`
- Audio Bitrate: `192`
- Encoder: Hardware (NVENC) or x264

### 3. Video Settings:
- Settings → Video
- Base Canvas: `1920x1080`
- Output Scaled: `1280x720`
- FPS: `30`

### 4. Audio Settings:
- Settings → Audio
- Sample Rate: `48000 Hz`
- Channels: `Stereo`

### 5. Scene Setup:
- Create scene: "MeetingScene"
- Add Sources:
  - Video Capture Device (your camera)
  - Audio Input Capture (your microphone)

### 6. Virtual Camera:
- Tools → Start Virtual Camera
- Verify "OBS Virtual Camera" appears in system

## Testing Checklist:
1. OBS shows camera preview (green light)
2. Audio meter moves when speaking
3. Virtual Camera shows active (green indicator)
4. Manual recording works in OBS
5. MeetingSense can connect to OBS WebSocket

## Troubleshooting:
- WebSocket connection fails? Check port 4455 firewall
- Virtual camera not showing? Restart OBS
- Recording corrupted? Use MP4 format, ensure disk space

## File Locations:
- Default recordings: `C:\Users\[Username]\Videos\`
- OBS config: `%APPDATA%\obs-studio\`