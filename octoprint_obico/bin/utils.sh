#!/bin/bash -e

debian_release() {
  cat /etc/debian_version | cut -d '.' -f1
}

debian_variant() {
  echo $( board_id ).debian.$( debian_release ).$( getconf LONG_BIT )-bit
}

board_id() {
    local model_file="/sys/firmware/devicetree/base/model"

    # Check for ARM boards first
    if [ -f "$model_file" ]; then
        if grep "Raspberry" $model_file >/dev/null; then
            echo "rpi"
            return
        elif grep -i "makerbase\|roc-rk3328-cc" $model_file >/dev/null; then
            echo "mks"
            return
        fi
    fi
    
    # Check for x86/x64 GPU using lspci
    if command -v lspci >/dev/null 2>&1; then
        local lspci_output=$(lspci 2>/dev/null | tr '[:upper:]' '[:lower:]')
        
        if echo "$lspci_output" | grep -q "intel.*vga"; then
            echo "intel"
            return
        elif echo "$lspci_output" | grep -E "amd|ati" | grep -q "vga\|display"; then
            echo "amd"
            return
        elif echo "$lspci_output" | grep -q "nvidia.*vga"; then
            echo "nvidia"
            return
        fi
    fi
    
    # Fallback: check /sys/class/drm for GPU vendor
    if [ -d "/sys/class/drm" ]; then
        for card in /sys/class/drm/card*/device/vendor; do
            if [ -f "$card" ] 2>/dev/null; then
                local vendor_id=$(cat "$card" 2>/dev/null)
                case "$vendor_id" in
                    "0x8086") echo "intel"; return ;;
                    "0x1002"|"0x1022") echo "amd"; return ;;
                    "0x10de") echo "nvidia"; return ;;
                esac
            fi
        done 2>/dev/null
    fi
    
    echo "NA"
}
