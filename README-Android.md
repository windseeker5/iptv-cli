# IPTV on Android — Termux Setup Guide

Run the IPTV CLI app on your Samsung tablet (or any Android device) using Termux. No root required.

---

## Step 1 — Install Required Android Apps

Install these two apps **from F-Droid** (not the Play Store — the Play Store version of Termux is outdated and broken):

1. **F-Droid** — open-source app store  
   → https://f-droid.org

2. **Termux** — Linux terminal for Android  
   → Search "Termux" in F-Droid

3. **mpv-android** — native video player (needed for stream playback)  
   → Search "mpv" in F-Droid  
   → Package ID: `is.xyz.mpv`

> **Why not Play Store?** The Termux version on the Play Store stopped receiving updates in 2020 and is incompatible with current packages.

---

## Step 2 — First-Time Termux Setup

Open Termux and run:

```bash
# Update all packages
pkg update && pkg upgrade -y
```

### Enable the extra keys bar (important for arrow keys in menus)
Press **Volume Up + Q** — this toggles a row of keys above your keyboard with arrow keys, Tab, Ctrl, etc. Arrow keys are required to navigate the IPTV menus.

---

## Step 3 — Install System Packages

```bash
# Core tools
pkg install -y python git openssh zsh neovim ffmpeg mpv

# Optional but useful
pkg install -y curl wget nano
```

This installs:
- `python` — Python 3.12 runtime
- `git` — to clone the repo
- `openssh` — SSH client AND server
- `zsh` — better shell
- `neovim` — terminal editor
- `ffmpeg` — needed for stream processing
- `mpv` — audio playback (video needs mpv-android, see Step 1)

---

## Step 4 — Set Up SSH

### SSH Client (connect FROM your tablet to other machines)
Already installed with `openssh`. Use it like normal:
```bash
ssh user@192.168.1.89
```

### SSH Server (connect TO your tablet from another machine)
Start the SSH server on your tablet:
```bash
# Start the SSH daemon
sshd

# Check what port it's on (default is 8022, not 22)
# Connect from another machine with:
# ssh -p 8022 your-tablet-ip
```

Set a password for your Termux user (required for SSH login):
```bash
passwd
```

To start SSH automatically when Termux opens, add it to your shell profile:
```bash
echo "sshd" >> ~/.bashrc
# or if using zsh:
echo "sshd" >> ~/.zshrc
```

---

## Step 5 — Set Zsh as Default Shell

```bash
chsh -s zsh
```

Close and reopen Termux for it to take effect.

---

## Step 6 — Clone the Repo

```bash
# Navigate to home directory
cd ~

# Clone the IPTV repo
git clone https://github.com/YOUR_USERNAME/iptv.git

# Go into the project folder
cd iptv
```

> Replace `YOUR_USERNAME/iptv` with your actual GitHub repo URL.

If your repo is private, set up an SSH key first:
```bash
# Generate SSH key
ssh-keygen -t ed25519 -C "android-tablet"

# Copy the public key and add it to GitHub → Settings → SSH Keys
cat ~/.ssh/id_ed25519.pub

# Then clone with SSH
git clone git@github.com:YOUR_USERNAME/iptv.git
```

---

## Step 7 — Configure Environment

```bash
# Copy the example config
cp .env.example .env

# Edit it with your IPTV credentials
nano .env
# or
nvim .env
```

Fill in these fields:
```
IPTV_SERVER_URL=http://your-provider-server.com
IPTV_USERNAME=your_username
IPTV_PASSWORD=your_password
```

The Docker/Nginx/Jellyfin settings in `.env` are not needed on Android — ignore them.

---

## Step 8 — Create Virtual Environment and Install Dependencies

```bash
# Create the venv
python -m venv venv

# Activate it
source venv/bin/activate

# Install all Python dependencies
pip install -r requirements.txt
```

> **Note:** Termux enforces virtual environments for pip (PEP 668). Always activate the venv before running the app. The app also does this automatically on startup.

---

## Step 9 — Run the App

```bash
# Make sure you're in the project folder with venv active
cd ~/iptv
source venv/bin/activate

python iptv.py
```

Use **arrow keys** (from the extra keys bar) to navigate the menus.

---

## Step 10 — Video Playback

### How it works on Android

The `mpv` command in Termux plays **audio only** — it has no video output on Android. For full video playback, the app launches **mpv-android** (the F-Droid app from Step 1) via an Android system intent automatically.

When you select a stream to play, mpv-android will open as a separate app with the video. Press Back to return to Termux.

### If video doesn't open
Make sure mpv-android (`is.xyz.mpv`) is installed from F-Droid. The package ID must match exactly.

---

## Quick Reference — Daily Use

```bash
# Open Termux, then:
cd ~/iptv
source venv/bin/activate
python iptv.py
```

### Update the app when new changes are pushed
```bash
cd ~/iptv
git pull
source venv/bin/activate
pip install -r requirements.txt   # only needed if requirements changed
python iptv.py
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Arrow keys don't work | Press **Volume Up + Q** to enable extra keys bar |
| `pip install` fails with "externally managed" | Activate the venv first: `source venv/bin/activate` |
| Video opens as audio only | Install mpv-android from F-Droid (`is.xyz.mpv`) |
| SSH connection refused | Run `sshd` in Termux first; use port `8022` not `22` |
| `pkg install` fails | Run `pkg update && pkg upgrade -y` first |
| App crashes on startup | Check your `.env` file has valid IPTV credentials |
| Git clone fails (private repo) | Set up SSH key and add it to GitHub (see Step 6) |
