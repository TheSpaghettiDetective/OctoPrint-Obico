# VA-API Hardware Acceleration Setup Guide

## Overview

OctoPrint-Obico now supports hardware-accelerated H.264 video encoding using VA-API (Video Acceleration API) for Intel and AMD GPUs. This significantly reduces CPU usage during webcam streaming, especially at higher resolutions and frame rates.

### Supported Platforms

| Platform | Hardware Encoder | Notes |
|----------|-----------------|-------|
| **Intel** (HD Graphics 2000+) | h264_vaapi, h264_qsv | Broadwell (Gen 8) and newer recommended |
| **AMD** (with AMDGPU driver) | h264_vaapi | Requires mesa-va-drivers |
| **Raspberry Pi** | h264_omx, h264_v4l2m2m | Already supported (no changes needed) |
| **Generic x86/x64** | Software fallback (MJPEG) | If no GPU or drivers |

---

## System Requirements

### Minimum Requirements

- **Operating System:** Linux (Ubuntu, Debian, Fedora, Arch, etc.)
- **GPU:** 
  - Intel HD Graphics 2000 or newer (Gen 5+)
  - AMD GPU with AMDGPU driver support
- **FFmpeg:** Version 4.0 or newer with VA-API support
- **Drivers:** Platform-specific VA-API drivers (see installation below)

### Recommended Requirements

- **FFmpeg:** Version 4.4 or newer
- **Intel:** 6th generation (Skylake) or newer for best performance
- **Mesa:** Version 20.0 or newer (for AMD)

---

## Installation Instructions

### Ubuntu / Debian

#### For Intel GPUs

**Modern Intel (Broadwell/Gen 8 and newer):**
```bash
sudo apt-get update
sudo apt-get install -y intel-media-va-driver intel-media-va-driver-non-free vainfo
```

**Legacy Intel (Ivy Bridge to Haswell/Gen 5-7):**
```bash
sudo apt-get update
sudo apt-get install -y i965-va-driver vainfo
```

#### For AMD GPUs

```bash
sudo apt-get update
sudo apt-get install -y mesa-va-drivers vainfo
```

#### Verify FFmpeg Support

```bash
# Check if FFmpeg has VA-API support
ffmpeg -encoders | grep vaapi

# Should show output like:
# V..... h264_vaapi           H.264/AVC (VAAPI) (codec h264)
```

If VA-API encoders are not listed, install FFmpeg:
```bash
sudo apt-get install -y ffmpeg libva-drm2
```

### Fedora / RHEL / CentOS

#### For Intel GPUs

```bash
# Modern Intel
sudo dnf install -y intel-media-driver libva-utils

# Legacy Intel
sudo dnf install -y libva-intel-driver libva-utils
```

#### For AMD GPUs

```bash
sudo dnf install -y mesa-va-drivers libva-utils
```

### Arch Linux

#### For Intel GPUs

```bash
# Modern Intel
sudo pacman -S intel-media-driver libva-utils

# Legacy Intel
sudo pacman -S libva-intel-driver libva-utils
```

#### For AMD GPUs

```bash
sudo pacman -S libva-mesa-driver libva-utils
```

---

## Permissions Setup

VA-API requires access to `/dev/dri/renderD*` devices. Add your user to the appropriate group:

### Check Current Permissions

```bash
ls -la /dev/dri/
```

You should see devices like `renderD128`. Check the group (usually `video` or `render`).

### Add User to Video Group

```bash
# Add user to video group
sudo usermod -a -G video $USER

# Or for render group
sudo usermod -a -G render $USER

# Log out and back in for changes to take effect
```

### Verify Group Membership

```bash
groups
# Should show: ... video ... (or render)
```

---

## Verification

### Test VA-API Installation

```bash
# Check VA-API drivers
vainfo

# Should show output like:
# libva info: VA-API version 1.x.x
# libva info: Trying to open /usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so
# ...
# vainfo: Driver version: Intel iHD driver - ...
```

### Test FFmpeg Hardware Encoding

```bash
# Create a test video (or use any video file)
# Test encoding with VA-API
ffmpeg -i input.mp4 -t 5 \
    -vaapi_device /dev/dri/renderD128 \
    -vf 'format=nv12,hwupload' \
    -c:v h264_vaapi \
    -b:v 2000k \
    test_output.mp4

# Check for errors - successful encoding means it's working
```

### Check OctoPrint-Obico Detection

After restarting OctoPrint, check the logs:

```bash
# Look for these log messages
grep -i "platform\|encoder\|vaapi" ~/.octoprint/logs/octoprint.log
```

You should see messages like:
```
INFO - octoprint.plugins.obico.hardware - Detected platform: intel
INFO - octoprint.plugins.obico.hardware - Detected GPU vendor: intel
INFO - octoprint.plugins.obico - Testing h264_vaapi encoder for platform: intel
INFO - octoprint.plugins.obico - Successfully detected h264_vaapi encoder
```

---

## Troubleshooting

### Issue: "Hardware encoder not detected"

**Possible Causes:**
1. VA-API drivers not installed
2. FFmpeg doesn't support VA-API
3. Missing `/dev/dri/renderD*` device

**Solutions:**

1. **Check if drivers are installed:**
   ```bash
   vainfo
   # Should show driver information, not errors
   ```

2. **Check FFmpeg VA-API support:**
   ```bash
   ffmpeg -encoders | grep vaapi
   # Should show h264_vaapi encoder
   ```

3. **Check DRM devices:**
   ```bash
   ls -la /dev/dri/
   # Should show renderD128 or similar
   ```

4. **Review OctoPrint logs:**
   ```bash
   tail -f ~/.octoprint/logs/octoprint.log | grep -i "encoder\|vaapi"
   ```

### Issue: "Permission denied on /dev/dri"

**Cause:** User doesn't have access to DRM devices.

**Solution:**
```bash
# Add user to video group
sudo usermod -a -G video $USER

# Log out and back in (or restart)
# Verify group membership
groups
```

### Issue: "vainfo: No VA display found"

**Possible Causes:**
1. Running over SSH without display
2. Drivers not loaded
3. GPU not detected

**Solutions:**

1. **Force DRM display:**
   ```bash
   vainfo --display drm --device /dev/dri/renderD128
   ```

2. **Check if GPU is detected:**
   ```bash
   lspci | grep -i "vga\|display"
   # Should show your Intel/AMD GPU
   ```

3. **Check kernel modules:**
   ```bash
   # For Intel
   lsmod | grep i915
   
   # For AMD
   lsmod | grep amdgpu
   ```

### Issue: "FFmpeg test timeout" or "Encoder test failed"

**Possible Causes:**
1. GPU busy with other processes
2. Driver issues
3. Incompatible FFmpeg version

**Solutions:**

1. **Check GPU usage:**
   ```bash
   # For Intel
   sudo intel_gpu_top
   
   # General
   ps aux | grep -i "ffmpeg\|vaapi"
   ```

2. **Check kernel messages:**
   ```bash
   sudo dmesg | grep -i "gpu\|drm\|i915\|amdgpu"
   ```

3. **Test manually:**
   ```bash
   # Try encoding a small test
   ffmpeg -f lavfi -i testsrc=duration=2:size=640x480:rate=25 \
       -vaapi_device /dev/dri/renderD128 \
       -vf 'format=nv12,hwupload' \
       -c:v h264_vaapi \
       -t 2 \
       -f null -
   ```

### Issue: "Streaming still uses high CPU"

**Possible Causes:**
1. Hardware encoder not being used (fell back to software)
2. Resolution/FPS too high for hardware
3. Multiple streams

**Solutions:**

1. **Check which encoder is active:**
   - Go to OctoPrint Settings → Obico → Troubleshooting
   - Look for "Hardware Encoder" status
   - Check logs for "Successfully detected" message

2. **Reduce resolution/FPS:**
   - Lower resolution to 720p or 480p
   - Reduce FPS to 15 or 25
   - Test if CPU usage improves

3. **Verify hardware encoding is working:**
   ```bash
   # While streaming, check if VA-API is active
   ps aux | grep ffmpeg
   # Should show command with -vaapi_device
   ```

### Issue: "Driver conflicts" or "Multiple drivers"

**Cause:** Multiple VA-API drivers installed (e.g., both iHD and i965).

**Solution:**

Force a specific driver:
```bash
# For modern Intel (iHD)
export LIBVA_DRIVER_NAME=iHD

# For legacy Intel (i965)
export LIBVA_DRIVER_NAME=i965

# Add to ~/.bashrc or /etc/environment for persistence
```

Or add to OctoPrint systemd service:
```bash
sudo systemctl edit octoprint.service
```

Add:
```ini
[Service]
Environment="LIBVA_DRIVER_NAME=iHD"
```

---

## Performance Expectations

### CPU Usage Reduction

With hardware acceleration enabled, you should see:

| Resolution | FPS | CPU Usage (Software) | CPU Usage (VA-API) | Reduction |
|-----------|-----|---------------------|-------------------|-----------|
| 480p | 25 | ~40-60% | ~5-15% | **~75%** |
| 720p | 25 | ~70-90% | ~10-20% | **~80%** |
| 1080p | 25 | ~100% | ~15-30% | **~70-85%** |

*Note: Results vary based on hardware, resolution, FPS, and system load.*

### Quality Considerations

Hardware encoders prioritize performance over quality:
- **Pros:** Much lower CPU usage, higher FPS possible, lower latency
- **Cons:** Slightly lower quality at same bitrate, less encoder tuning options

For most 3D printing monitoring, the quality difference is negligible.

---

## Docker Setup

If running OctoPrint-Obico in Docker, you need GPU passthrough:

### Update docker-compose.yml

```yaml
services:
  octoprint:
    devices:
      - /dev/dri:/dev/dri  # GPU passthrough
    environment:
      - LIBVA_DRIVER_NAME=iHD  # Optional: specify driver
```

### Test in Container

```bash
docker compose exec octoprint vainfo
# Should show VA-API driver info
```

---

## Advanced Configuration

### Custom VA-API Device

If you have multiple GPUs or a non-standard device path:

The encoder will automatically try `/dev/dri/renderD128`, but you can verify:

```bash
# List all render devices
ls -la /dev/dri/renderD*

# Test specific device
vainfo --device /dev/dri/renderD129
```

### Encoder Quality Tuning

VA-API encoders use hardware defaults. For advanced users, you can modify the FFmpeg command in the code, but this is not recommended unless you know what you're doing.

---

## FAQ

### Q: Will this work on Windows or macOS?

**A:** No, VA-API is Linux-only. Windows uses DXVA2/D3D11VA, and macOS uses VideoToolbox. These may be added in future updates.

### Q: Does this work with NVIDIA GPUs?

**A:** Not yet. NVIDIA uses NVENC, which requires different FFmpeg flags and CUDA runtime. Support may be added in the future.

### Q: Will this break my Raspberry Pi setup?

**A:** No! Raspberry Pi continues to use h264_omx or h264_v4l2m2m encoders as before. VA-API is only used on x86/x64 systems with compatible GPUs.

### Q: Can I force software encoding even if I have a GPU?

**A:** Currently, the plugin auto-detects and uses hardware encoding when available. You can disable video streaming entirely in settings, but there's no option to force software H.264 (it would fall back to MJPEG instead).

### Q: What if I have hybrid graphics (Intel + NVIDIA)?

**A:** The plugin will detect and use the Intel iGPU for encoding, which works well. NVIDIA discrete GPU encoding (NVENC) is not yet supported.

### Q: Does this work in VMs or LXC containers?

**A:** Depends on GPU passthrough configuration:
- **VMs:** Requires GPU passthrough (difficult with most hypervisors)
- **LXC:** Usually works with proper device mapping
- **Docker:** Works with `--device /dev/dri:/dev/dri`

---

## Reporting Issues

If you encounter problems:

1. **Gather Information:**
   ```bash
   # System info
   uname -a
   lspci | grep -i vga
   vainfo
   ffmpeg -version
   
   # OctoPrint logs
   tail -100 ~/.octoprint/logs/octoprint.log
   ```

2. **Enable Debug Logging:**
   - OctoPrint Settings → Logging
   - Set `octoprint.plugins.obico` to `DEBUG`
   - Restart OctoPrint
   - Reproduce the issue
   - Check logs

3. **Report on GitHub:**
   - https://github.com/TheSpaghettiDetective/OctoPrint-Obico/issues
   - Include system info and logs
   - Describe expected vs actual behavior

---

## Additional Resources

- **VA-API Documentation:** https://01.org/linuxgraphics/community/vaapi
- **FFmpeg VA-API Guide:** https://trac.ffmpeg.org/wiki/Hardware/VAAPI
- **Intel Media Driver:** https://github.com/intel/media-driver
- **Mesa3D:** https://docs.mesa3d.org/
- **Obico Discord:** https://discord.com/invite/NcZkQfj

---

**Last Updated:** October 31, 2025  
**Plugin Version:** 2.5.4+
