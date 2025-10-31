# coding=utf-8
"""
Hardware detection and capability checking for OctoPrint-Obico.
Detects platform, GPU vendor, and available hardware acceleration.
"""

import os
import re
import subprocess
import logging
from typing import Optional, Dict, List

_logger = logging.getLogger('octoprint.plugins.obico.hardware')


class HardwareCapabilities:
    """Detect and query hardware capabilities for video encoding."""
    
    def __init__(self):
        self._platform = None
        self._gpu_vendor = None
        self._vaapi_device = None
        self._capabilities_checked = False
    
    def detect_platform(self) -> str:
        """
        Detect the hardware platform.
        
        Returns:
            str: 'rpi', 'intel', 'amd', 'nvidia', or 'generic'
        """
        if self._platform:
            return self._platform
        
        # Check for Raspberry Pi
        if self._is_raspberry_pi():
            self._platform = 'rpi'
            return self._platform
        
        # Check for x86/x64 with GPU
        gpu_vendor = self.detect_gpu_vendor()
        if gpu_vendor:
            self._platform = gpu_vendor
        else:
            self._platform = 'generic'
        
        _logger.info(f'Detected platform: {self._platform}')
        return self._platform
    
    def detect_gpu_vendor(self) -> Optional[str]:
        """
        Detect GPU vendor on x86/x64 systems.
        
        Returns:
            str: 'intel', 'amd', 'nvidia', or None
        """
        if self._gpu_vendor:
            return self._gpu_vendor
        
        try:
            # Try lspci first
            result = subprocess.run(
                ['lspci'], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            
            if result.returncode == 0:
                output = result.stdout.lower()
                
                if 'intel' in output and 'vga' in output:
                    self._gpu_vendor = 'intel'
                elif 'amd' in output or 'ati' in output:
                    if 'vga' in output or 'display' in output:
                        self._gpu_vendor = 'amd'
                elif 'nvidia' in output and 'vga' in output:
                    self._gpu_vendor = 'nvidia'
            
            # Fallback: check /sys/class/drm
            if not self._gpu_vendor:
                self._gpu_vendor = self._detect_gpu_from_drm()
            
        except Exception as e:
            _logger.debug(f'Failed to detect GPU vendor: {e}')
        
        if self._gpu_vendor:
            _logger.info(f'Detected GPU vendor: {self._gpu_vendor}')
        return self._gpu_vendor
    
    def has_vaapi_support(self) -> bool:
        """
        Check if VA-API is available on the system.
        
        Returns:
            bool: True if VA-API devices are present
        """
        vaapi_device = self.get_vaapi_device()
        if not vaapi_device:
            return False
        
        # Try to run vainfo if available
        try:
            result = subprocess.run(
                ['vainfo', '--display', 'drm', '--device', vaapi_device],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                _logger.debug(f'VA-API supported. vainfo output: {result.stdout[:200]}')
                return True
            else:
                _logger.debug(f'VA-API check failed: {result.stderr[:200]}')
                
        except FileNotFoundError:
            _logger.debug('vainfo command not found, assuming VA-API may still work')
            return True  # Device exists, assume it works
        except Exception as e:
            _logger.debug(f'VA-API check error: {e}')
        
        return False
    
    def get_vaapi_device(self) -> Optional[str]:
        """
        Get the VA-API device path.
        
        Returns:
            str: Path like '/dev/dri/renderD128' or None
        """
        if self._vaapi_device:
            return self._vaapi_device
        
        # Check for DRM render devices
        drm_dir = '/dev/dri'
        if not os.path.exists(drm_dir):
            return None
        
        # Look for renderD* devices
        try:
            devices = os.listdir(drm_dir)
            render_devices = [d for d in devices if d.startswith('renderD')]
            
            if render_devices:
                # Use the first render device (usually renderD128)
                render_devices.sort()
                self._vaapi_device = os.path.join(drm_dir, render_devices[0])
                _logger.debug(f'Found VA-API device: {self._vaapi_device}')
                return self._vaapi_device
                
        except Exception as e:
            _logger.debug(f'Error checking DRM devices: {e}')
        
        return None
    
    def get_capabilities_info(self) -> Dict:
        """
        Get comprehensive hardware capabilities info.
        
        Returns:
            dict: Hardware capabilities and status
        """
        return {
            'platform': self.detect_platform(),
            'gpu_vendor': self.detect_gpu_vendor(),
            'vaapi_supported': self.has_vaapi_support(),
            'vaapi_device': self.get_vaapi_device(),
            'recommended_encoder': self.get_recommended_encoder(),
        }
    
    def get_recommended_encoder(self) -> Optional[str]:
        """
        Get the recommended encoder for this hardware.
        
        Returns:
            str: Encoder name or None
        """
        platform = self.detect_platform()
        
        RECOMMENDATIONS = {
            'intel': 'h264_vaapi or h264_qsv',
            'amd': 'h264_vaapi',
            'rpi': 'h264_omx or h264_v4l2m2m',
            'generic': 'h264_vaapi (if available)',
        }
        
        return RECOMMENDATIONS.get(platform, 'None available')
    
    # Private helper methods
    
    def _is_raspberry_pi(self) -> bool:
        """Check if running on Raspberry Pi."""
        try:
            with open('/sys/firmware/devicetree/base/model', 'r') as f:
                model = f.read()
                return 'Raspberry Pi' in model
        except:
            return False
    
    def _detect_gpu_from_drm(self) -> Optional[str]:
        """Detect GPU vendor from /sys/class/drm."""
        try:
            drm_path = '/sys/class/drm'
            if not os.path.exists(drm_path):
                return None
            
            for card in os.listdir(drm_path):
                if not card.startswith('card'):
                    continue
                
                device_path = os.path.join(drm_path, card, 'device', 'vendor')
                if not os.path.exists(device_path):
                    continue
                
                with open(device_path, 'r') as f:
                    vendor_id = f.read().strip()
                
                # PCI vendor IDs
                if vendor_id == '0x8086':
                    return 'intel'
                elif vendor_id in ['0x1002', '0x1022']:
                    return 'amd'
                elif vendor_id == '0x10de':
                    return 'nvidia'
        
        except Exception as e:
            _logger.debug(f'Failed to detect GPU from DRM: {e}')
        
        return None
