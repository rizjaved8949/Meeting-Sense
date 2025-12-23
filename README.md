# 🎯 Smart Meeting Manager - AI-Powered Professional Meeting System

> A comprehensive meeting management system with AI-driven attendance tracking, automated transcription, and professional reporting

![MeetingSense](https://img.shields.io/badge/MeetingSense-AI%20Powered-blue)
![Python](https://img.shields.io/badge/Python-3.8%2B-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104%2B-red)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ Features

### 🎥 **Smart Attendance System**
- **Face Recognition**: Real-time attendance tracking with YOLO and InsightFace
- **Voice Identification**: Speaker recognition using ECAPA-TDNN embeddings
- **Live Camera Feed**: WebSocket-based live video streaming
- **Zoom Control**: Dynamic camera zoom adjustment
- **Attendance Reports**: Automated Excel report generation

### 🎙️ **Intelligent Meeting Recording**
- **Audio Recording**: High-quality audio capture and processing
- **Video Recording**: OBS Studio integration for professional recordings
- **AI Transcription**: Whisper Large-v3 for accurate speech-to-text
- **Speaker Diarization**: Automatic speaker identification and segmentation
- **Dual WebSocket**: Separate connections for attendance and OBS live feed

### 📊 **Professional Meeting Analytics**
- **AI Summarization**: LLM-powered meeting summaries (OpenAI compatible)
- **Transcript PDF**: Clean, formatted transcript documents
- **Action Items**: Automatic task extraction and assignment
- **Attendance Analytics**: Real-time statistics and reporting
- **Email Automation**: Automated report distribution

### 🔧 **System Architecture**
- **Modern Stack**: FastAPI + WebSocket + React-like frontend
- **GPU Acceleration**: CUDA support for face recognition and AI processing
- **OBS Integration**: Professional video recording via OBS WebSocket
- **Modular Design**: Separate components for easy maintenance
- **Environment Config**: Secure configuration via .env files

## 🚀 Quick Start

### Prerequisites
```bash
# System Requirements
- Python 3.12.0
- OBS Studio 32.0.2 or higher (required for video recording)
- Webcam and microphone
- CUDA-capable GPU (optional but recommended for acceleration)
- At least 8GB RAM
- Windows 10/11
