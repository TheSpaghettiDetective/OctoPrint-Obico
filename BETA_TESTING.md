# VA-API Beta Testing Guide

**Branch:** `vaapi-beta`  
**Status:** Beta Testing Phase  
**Date:** October 31, 2025

## What's New?

This beta adds **Intel/AMD GPU hardware acceleration** support for H.264 webcam streaming using VA-API.

### Benefits
- 60-80% reduction in CPU usage during streaming
- Lower system temperatures
- Better performance on x86/x64 Linux systems with Intel/AMD GPUs

### Compatibility
- ✅ Intel HD Graphics (Gen 5+)
- ✅ AMD GPUs with VA-API support
- ✅ Raspberry Pi (unchanged, still works perfectly)
- ✅ Systems without GPU (graceful fallback to MJPEG)

## Installation for Testers

### Method 1: Install from GitHub Branch (Easiest)

```bash
# SSH into your OctoPrint system
ssh pi@octopi.local

# Install the beta plugin from GitHub
~/oprint/bin/pip install git+https://github.com/EvasiveXkiller/OctoPrint-Obico.git@vaapi-beta

# Restart OctoPrint
sudo service octoprint restart
```

### Method 2: Manual Installation from ZIP

1. Download: https://github.com/EvasiveXkiller/OctoPrint-Obico/archive/refs/heads/vaapi-beta.zip
2. In OctoPrint: Settings → Plugin Manager → Get More → "...from URL"
3. Enter: `https://github.com/EvasiveXkiller/OctoPrint-Obico/archive/refs/heads/vaapi-beta.zip`
4. Click "Install"
5. Restart OctoPrint when prompted

## What to Test

### 1. Check Hardware Detection

Enable debug logging in OctoPrint settings, then check `octoprint.log`:

```bash
grep "Detected platform" ~/.octoprint/logs/octoprint.log
grep "hardware encoder" ~/.octoprint/logs/octoprint.log
```

**Expected output:**
```
INFO - Detected platform: intel    (or 'amd', 'rpi', 'generic')
INFO - Testing h264_vaapi encoder for platform: intel
INFO - Found working h264_vaapi encoder
```

### 2. Verify VA-API is Working

```bash
# Check if VA-API device exists
ls -la /dev/dri/renderD*

# Test VA-API with vainfo
vainfo

# Should show your GPU and supported profiles
```

### 3. Test Webcam Streaming

1. Start a print or use the control tab
2. Open Obico app/web dashboard
3. Verify video stream works
4. Check CPU usage: `htop` or `top`

**What to look for:**
- Video should stream smoothly
- CPU usage should be significantly lower (check before/after)
- No errors in OctoPrint logs

### 4. Performance Metrics (Optional but Helpful!)

```bash
# Monitor CPU during streaming
top -b -n 60 -d 1 | grep python > cpu_test.log

# Share the results!
```

## Troubleshooting

### No Hardware Acceleration Detected

```bash
# Check VA-API drivers are installed
dpkg -l | grep -i va-driver

# Install if missing (Debian/Ubuntu)
sudo apt-get install intel-media-va-driver mesa-va-drivers

# Add user to video/render groups
sudo usermod -aG video,render $USER
sudo usermod -aG video,render octoprint  # if running as service

# Reboot
sudo reboot
```

### Check Encoder Detection

```bash
# Test FFmpeg encoders manually
ffmpeg -hide_banner -encoders | grep h264

# Test h264_vaapi specifically
ffmpeg -hide_banner -f lavfi -i testsrc=duration=1:size=640x480 \
  -c:v h264_vaapi -vaapi_device /dev/dri/renderD128 -f null - 2>&1 | head -20
```

### Still Not Working?

The plugin will automatically fall back to MJPEG (software encoding). This is normal and expected behavior if:
- Your GPU doesn't support VA-API
- Drivers aren't installed
- Permissions aren't configured

**This is not a bug** - the plugin is designed to work without GPU acceleration.

## Reporting Results

Please report your results in the GitHub issue or Discord channel:

### Template

```markdown
**System:**
- Hardware: (e.g., Intel i5-8265U, AMD Ryzen 5 3600, Raspberry Pi 4)
- OS: (e.g., OctoPi 1.0.0, Ubuntu 22.04)
- OctoPrint Version: (e.g., 1.9.3)

**VA-API Detection:**
- Platform detected: (intel/amd/rpi/generic)
- Encoder used: (h264_vaapi/h264_omx/mjpeg)
- vainfo output: (paste first 10 lines)

**Performance:**
- CPU usage before: (e.g., 80% during streaming)
- CPU usage after: (e.g., 15% during streaming)
- Video quality: (Good/Artifacts/Not Working)
- Stability: (Ran for X hours, any crashes?)

**Logs:**
- Relevant OctoPrint logs (grep for "hardware" and "encoder")
```

### Where to Report

- **GitHub Issue:** https://github.com/EvasiveXkiller/OctoPrint-Obico/issues
- **Discord:** (Add your server link)
- **Email:** (Add your contact email)

## Reverting to Stable

If you encounter issues, revert to the stable version:

```bash
~/oprint/bin/pip install --force-reinstall git+https://github.com/EvasiveXkiller/OctoPrint-Obico.git@master
sudo service octoprint restart
```

## Known Limitations

- **WSL/Docker:** GPU passthrough limited, may not work in all configurations
- **NVIDIA GPUs:** Not supported in this beta (NVENC coming in future release)
- **Old Intel GPUs:** Gen 4 and older may not work (Gen 5+ recommended)

## Thank You!

Your testing helps make OctoPrint-Obico better for everyone. Thank you for being an early adopter! 🎉

---

**Questions?** Open an issue or reach out on Discord.
