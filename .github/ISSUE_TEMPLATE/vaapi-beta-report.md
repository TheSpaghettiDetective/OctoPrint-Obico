---
name: VA-API Beta Test Report
about: Report your VA-API beta testing results
title: '[VA-API Beta] Testing on [YOUR_HARDWARE]'
labels: beta-testing, vaapi
assignees: ''

---

## System Information

**Hardware:**
- CPU: (e.g., Intel i5-8265U, AMD Ryzen 5 3600, Raspberry Pi 4)
- GPU: (e.g., Intel UHD Graphics 620, AMD Radeon RX 580)
- RAM: (e.g., 8GB)

**Software:**
- OS: (e.g., OctoPi 1.0.0, Ubuntu 22.04, Debian 11)
- OctoPrint Version: (e.g., 1.9.3)
- Python Version: (e.g., 3.9.2)
- Obico Plugin Version: (check in Plugin Manager)

## Installation Method

- [ ] Installed from GitHub branch (`pip install git+https://...`)
- [ ] Installed from ZIP URL
- [ ] Other: (please describe)

## Hardware Detection Results

**Platform Detected:**
```
# Paste output from:
grep "Detected platform" ~/.octoprint/logs/octoprint.log
```

**Encoder Used:**
```
# Paste output from:
grep "encoder" ~/.octoprint/logs/octoprint.log | tail -10
```

**VA-API Device Check:**
```bash
# Paste output from:
ls -la /dev/dri/
vainfo
```

## Performance Testing

**CPU Usage Comparison:**
- Before (stable version): ___% during streaming
- After (beta version): ___% during streaming
- Improvement: ___%

**Streaming Quality:**
- [ ] Excellent - smooth, no artifacts
- [ ] Good - occasional minor issues
- [ ] Poor - frequent artifacts or stuttering
- [ ] Not working - no video stream

**Stability:**
- Tested for: ___ hours
- [ ] No crashes or issues
- [ ] Some issues (describe below)
- [ ] Critical problems (describe below)

## Issues Encountered

(Describe any problems, errors, or unexpected behavior)

**Error Logs:**
```
# Paste relevant OctoPrint logs here
```

## Additional Notes

(Any other observations, suggestions, or comments)

## Would you recommend this beta?

- [ ] Yes, works great!
- [ ] Yes, with minor issues
- [ ] No, needs more work
- [ ] Unsure/Need more testing

---

**Thank you for testing!** 🎉
