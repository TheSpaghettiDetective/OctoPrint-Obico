# OctoPrint-NozzleCam Configuration
## _First Layer AI Failure Detection_

[![N|Solid](https://play-lh.googleusercontent.com/rz4zy00-EI-LrPVaXw96YRcvh8rByPSBGH5JY9dK7h4niwzQAVnKfb8oX2J1v9mCjg=w3840-h2160-rw)](https://obico.io)

# Requirements

- Nozzle camera set up & configured in OctoPrint 
- Configure you nozzle camera snapshot url in Obico (You should have received this in the sign up email)

This set up is much simpler than moonraker / klipper but here is a more detailed walkthrough if need be.

### Nozzle Camera
Pretty straightforward. 
- Buy & attach a nozzle camera to your 3D printer. Connect the USB to your Pi / SBC.
- Once it's all connected, configure your new camera in OctoPrint.
- Make sure the snapshot URL is working. (you will need this URL for the next step)

### Installation
If you have received the Alpha testing sign up link, open the configuration URL.
- Select the printer you'd like to configure from the dropdown. 
- Take the snapshot URL from the previous step & insert it into the text input.
- Click save.

### Layer Configuration

Please make sure layer progress is working properly / as expected. First layer shows 1/xxx in Obico etc. This is crutial to the success of this model. 

We preprocess every file on OctoPrint to find layer changes so no action needed on your end, just make sure it is working properly.

# Thank you!
