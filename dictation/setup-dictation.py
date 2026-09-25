#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup-dictation.py
==================
Sets up voice typing on Debian 13 + KDE Plasma (Wayland), like Win+H on
Windows:
press Super+H, speak, and the text appears in the focused window. Speech is
transcribed in the cloud by Groq (Whisper large-v3, free API key) or OpenAI;
see dictation.py next to this script.

What it does:
  1. Installs the system packages (python3-venv, wl-clipboard, pipewire-bin,
     ...).
  2. Lets the logged-in user create a virtual keyboard (/dev/uinput), which
     is needed to paste the text. (udev rule + uinput module at boot.)
  3. Creates a Python venv in ~/.local/share/dictation with faster-whisper
     (only its voice activity detector is used, to detect pauses) and copies
     dictation.py there.
  4. Writes ~/.config/dictation/config.json (backend, language, ...); an
     existing config is kept, --backend / --language update it. The
     language is the one of your desktop (English, Hungarian, ...).
  5. Sets up the API key if there is none yet, or when --api-key is given:
     it opens the key page of the service in the browser, explains the
     clicks, notices the key by itself when you copy it (or you paste it),
     checks it with the service and saves it to ~/.config/dictation/api-key
     (readable only by you). A key in $GROQ_API_KEY / $OPENAI_API_KEY is
     used without asking.
  6. Creates and starts the 'dictation' systemd user service.
  7. Binds Super+H to start/stop dictation, with a global shortcut of a
     hidden 'Dictation' application.
  8. Checks the microphone level and turns the input volume down if the
     recording is clipped (distorted), which ruins recognition.

Run it as your normal user (NOT with sudo); it asks for the sudo password
for steps 1-2. Safe to run again.

API key:
    groq    (default) free: https://console.groq.com/keys
    openai  paid:       https://platform.openai.com/api-keys

USAGE:
    python3 setup-dictation.py
    python3 setup-dictation.py --api-key                 # change the key
    python3 setup-dictation.py --language hu             # dictate in Hungarian
    python3 setup-dictation.py --language ""             # detect the language
    python3 setup-dictation.py --backend openai
    python3 setup-dictation.py --uninstall
"""

import argparse
import getpass
import json
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.expanduser("~/.local/share/dictation")
VENV = os.path.join(APP_DIR, "venv")
CONFIG_FILE = os.path.expanduser("~/.config/dictation/config.json")
API_KEY_FILE = os.path.expanduser("~/.config/dictation/api-key")
KEY_PAGES = {"groq": "https://console.groq.com/keys",
             "openai": "https://platform.openai.com/api-keys"}
# What a key looks like, to spot it on the clipboard.
KEY_PATTERNS = {"groq": r"gsk_[A-Za-z0-9]{20,}",
                "openai": r"sk-[A-Za-z0-9_-]{20,}"}
# Listing the models needs a valid key and costs nothing.
KEY_CHECK_URLS = {"groq": "https://api.groq.com/openai/v1/models",
                  "openai": "https://api.openai.com/v1/models"}
KEY_ENV = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}
KEY_GUIDES = {
    ("groq", "en"): """
How to get a free Groq API key (about a minute):
  1. The Groq console opens in your browser: {page}
  2. Sign in with Google, GitHub or your e-mail address.
     It is free, no credit card is needed.
  3. Click "Create API Key", type any name (e.g. dictation), click Submit.
  4. Click the copy button next to the new key. Groq shows it only once.
The copied key is picked up from the clipboard here by itself, checked and
saved. You can also paste it here (it stays hidden) and press Enter.
""",
    ("groq", "hu"): """
Így kapsz ingyenes Groq API-kulcsot (kb. egy perc):
  1. A böngészőben megnyílik a Groq konzol: {page}
  2. Jelentkezz be Google-, GitHub-fiókkal vagy e-mail-címmel.
     Ingyenes, bankkártya nem kell.
  3. Kattints a "Create API Key" gombra, adj neki bármilyen nevet
     (pl. diktalas), majd kattints a Submit gombra.
  4. Kattints az új kulcs melletti másolás gombra. A Groq csak egyszer
     mutatja meg.
A kimásolt kulcsot ez a program magától észreveszi a vágólapon,
ellenőrzi és elmenti. Ide is beillesztheted (nem látszik), majd Enter.
""",
    ("openai", "en"): """
How to get an OpenAI API key (paid, billed per minute of audio):
  1. The OpenAI platform opens in your browser: {page}
  2. Sign in, and add a payment method under Settings > Billing.
  3. Click "Create new secret key", then copy the key.
The copied key is picked up from the clipboard here by itself, checked and
saved. You can also paste it here (it stays hidden) and press Enter.
""",
    ("openai", "hu"): """
Így kapsz OpenAI API-kulcsot (fizetős, a hang perce szerint számláz):
  1. A böngészőben megnyílik az OpenAI platform: {page}
  2. Jelentkezz be, és a Settings > Billing alatt adj meg fizetési módot.
  3. Kattints a "Create new secret key" gombra, és másold ki a kulcsot.
A kimásolt kulcsot ez a program magától észreveszi a vágólapon,
ellenőrzi és elmenti. Ide is beillesztheted (nem látszik), majd Enter.
""",
}
SERVICE_FILE = os.path.expanduser("~/.config/systemd/user/dictation.service")
LAUNCHER_DIR = os.path.expanduser("~/.local/bin")
LAUNCHER = os.path.join(LAUNCHER_DIR, "dictation")

UDEV_RULE_FILE = "/etc/udev/rules.d/60-dictation-uinput.rules"
UDEV_RULE = ('KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", '
             'OPTIONS+="static_node=uinput"\n')
MODULES_FILE = "/etc/modules-load.d/dictation-uinput.conf"

# libglib2.0-bin provides gdbus, used for the notifications.
APT_PACKAGES = ["python3-venv", "wl-clipboard", "pipewire-bin",
                "libglib2.0-bin", "libnotify-bin"]

KDE_DESKTOP_FILE = os.path.expanduser(
    "~/.local/share/applications/dictation.desktop")
KDE_HOTKEY = "Meta+H"
KDE_DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Dictation
Name[hu]=Diktálás
Comment=Start or stop voice typing
Comment[hu]=Hangalapú gépelés indítása vagy leállítása
Icon=audio-input-microphone
Exec={launcher} toggle
NoDisplay=true
X-KDE-Shortcuts={hotkey}
"""

SERVICE = """[Unit]
Description=Dictation (voice typing, Super+H)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart={venv}/bin/python {app}/dictation.py daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""


def run(cmd, **kwargs):
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kwargs)


def desktop_language():
    """The desktop's language as a two-letter code, e.g. 'hu' or 'en'."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").split(":")[0].split(".")[0]
        if value and value not in ("C", "POSIX"):
            return value.split("_")[0].lower()
    return "en"


def sudo_write(path, content):
    print("+ write %s" % path)
    subprocess.run(["sudo", "mkdir", "-p", os.path.dirname(path)], check=True)
    subprocess.run(["sudo", "tee", path], input=content, text=True,
                   stdout=subprocess.DEVNULL, check=True)


# ---------------------------------------------------------------- install

def install_system():
    print("==> Installing system packages")
    run(["sudo", "apt-get", "update"])
    run(["sudo", "apt-get", "install", "-y"] + APT_PACKAGES)

    print("==> Allowing the virtual keyboard (/dev/uinput)")
    sudo_write(UDEV_RULE_FILE, UDEV_RULE)
    sudo_write(MODULES_FILE, "uinput\n")
    run(["sudo", "modprobe", "uinput"])
    run(["sudo", "udevadm", "control", "--reload-rules"])
    run(["sudo", "udevadm", "trigger", "--action=change",
         "--name-match=uinput"])
    run(["sudo", "udevadm", "settle"])
    if not os.access("/dev/uinput", os.W_OK):
        print("! /dev/uinput is not writable yet; log out and back in "
              "(or reboot) before using dictation.")


def install_app():
    print("==> Installing faster-whisper (voice activity detector) into %s"
          % VENV)
    os.makedirs(APP_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(VENV, "bin", "python")):
        run([sys.executable, "-m", "venv", VENV])
    run([os.path.join(VENV, "bin", "pip"), "install", "--upgrade", "--quiet",
         "pip", "faster-whisper"])
    shutil.copy(os.path.join(HERE, "dictation.py"),
                os.path.join(APP_DIR, "dictation.py"))

    os.makedirs(LAUNCHER_DIR, exist_ok=True)
    with open(LAUNCHER, "w", encoding="utf-8") as f:
        f.write('#!/bin/sh\nexec python3 "%s/dictation.py" "$@"\n' % APP_DIR)
    os.chmod(LAUNCHER, 0o755)


def install_config(args):
    print("==> Writing %s" % CONFIG_FILE)
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    config = {}
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    config.setdefault("backend", "groq")
    config.setdefault("language", desktop_language())
    if args.backend:
        config["backend"] = args.backend
    if args.language is not None:
        config["language"] = args.language
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("    backend=%s language=%s" % (config["backend"],
                                         config["language"] or "auto"))

    has_key = os.path.isfile(API_KEY_FILE) and os.path.getsize(API_KEY_FILE)
    if has_key and not args.api_key:
        return
    key = obtain_key(config["backend"])
    if not key:
        print("! No API key saved; dictation will not work until you run: "
              "python3 %s --api-key" % os.path.abspath(sys.argv[0]))
        return
    fd = os.open(API_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    os.chmod(API_KEY_FILE, 0o600)
    print("    saved to %s (only you can read it)" % API_KEY_FILE)


# ----------------------------------------------------------------- API key

def check_key(backend, key):
    """True if the service accepts the key, False if it refuses it, None if
    the service could not be reached."""
    request = urllib.request.Request(KEY_CHECK_URLS[backend], headers={
        "Authorization": "Bearer " + key, "User-Agent": "setup-dictation.py"})
    try:
        with urllib.request.urlopen(request, timeout=15):
            return True
    except urllib.error.HTTPError as exc:
        return False if exc.code in (401, 403) else None
    except (urllib.error.URLError, OSError):
        return None


def clipboard_key(backend):
    """The API key on the clipboard, or None."""
    if not os.environ.get("WAYLAND_DISPLAY") or not shutil.which("wl-paste"):
        return None
    result = subprocess.run(["wl-paste", "--no-newline"], capture_output=True,
                            timeout=5)
    text = result.stdout.decode("utf-8", "replace").strip()
    return text if re.fullmatch(KEY_PATTERNS[backend], text) else None


def wait_for_key(backend, rejected):
    """Wait until a key is copied to the clipboard or typed in; '' if the
    user only presses Enter."""
    if not sys.stdin.isatty():
        return getpass.getpass("API key (Enter to skip): ").strip()
    print("API key (Enter to skip): ", end="", flush=True)
    old = termios.tcgetattr(sys.stdin)
    hidden = old[:]
    hidden[3] &= ~termios.ECHO
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, hidden)
    try:
        while True:
            key = clipboard_key(backend)
            if key and key not in rejected:
                print("(found on the clipboard)")
                return key
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                print()
                return sys.stdin.readline().strip()
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def obtain_key(backend):
    """Get a working API key for the backend, or '' if the user skips."""
    env = os.environ.get(KEY_ENV[backend], "").strip()
    if env and check_key(backend, env) is not False:
        print("    using the API key in $%s" % KEY_ENV[backend])
        return env

    language = "hu" if desktop_language() == "hu" else "en"
    page = KEY_PAGES[backend]
    print(KEY_GUIDES[(backend, language)].format(page=page))
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        subprocess.run(["xdg-open", page], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    rejected = set()
    while True:
        try:
            key = wait_for_key(backend, rejected)
        except (EOFError, KeyboardInterrupt):
            print()
            return ""
        if not key:
            return ""
        from_clipboard = key == clipboard_key(backend)
        valid = check_key(backend, key)
        if valid is False:
            rejected.add(key)
            print("! %s does not accept this key; copy the new key again, "
                  "or paste it here." % backend)
            continue
        if valid is None:
            print("! Could not reach %s to check the key; saving it "
                  "anyway." % backend)
        else:
            print("    the key works")
        if from_clipboard:
            # The key has no business staying on the clipboard.
            subprocess.run(["wl-copy", "--clear"])
        return key


def install_service():
    print("==> Starting the 'dictation' user service")
    os.makedirs(os.path.dirname(SERVICE_FILE), exist_ok=True)
    with open(SERVICE_FILE, "w", encoding="utf-8") as f:
        f.write(SERVICE.format(venv=VENV, app=APP_DIR))
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "dictation.service"])
    run(["systemctl", "--user", "restart", "dictation.service"])


def install_hotkey_kde():
    # A hidden application whose global shortcut runs the toggle command:
    # the same thing System Settings > Shortcuts > Add Command creates.
    print("==> Binding %s to dictation" % KDE_HOTKEY)
    os.makedirs(os.path.dirname(KDE_DESKTOP_FILE), exist_ok=True)
    with open(KDE_DESKTOP_FILE, "w", encoding="utf-8") as f:
        f.write(KDE_DESKTOP_ENTRY.format(launcher=LAUNCHER,
                                         hotkey=KDE_HOTKEY))
    run(["kwriteconfig6", "--file", "kglobalshortcutsrc",
         "--group", "services", "--group", "dictation.desktop",
         "--key", "_launch", KDE_HOTKEY])
    subprocess.run(["kbuildsycoca6"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    print("    the shortcut works after you log out and back in")


def uninstall_hotkey_kde():
    if os.path.exists(KDE_DESKTOP_FILE):
        os.remove(KDE_DESKTOP_FILE)
    subprocess.run(["kwriteconfig6", "--file", "kglobalshortcutsrc",
                    "--group", "services", "--group", "dictation.desktop",
                    "--key", "_launch", "--delete"])
    subprocess.run(["kbuildsycoca6"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def mic_clipping():
    """Record 1.5 s and return the share of clipped samples (0..1)."""
    rec = subprocess.Popen(["pw-record", "--raw", "--rate", "16000",
                            "--channels", "1", "--format", "s16", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    rec.terminate()
    data = rec.stdout.read()
    rec.wait()
    samples = memoryview(data[:len(data) // 2 * 2]).cast("h")
    if not len(samples):
        return None
    return sum(1 for s in samples if abs(s) > 32000) / len(samples)


def check_microphone():
    print("==> Checking the microphone level (stay quiet for a moment)")
    for _ in range(8):
        clipped = mic_clipping()
        if clipped is None:
            print("! No sound from the microphone; check the sound settings.")
            return
        volume = subprocess.run(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"],
            capture_output=True, text=True).stdout.split()
        level = float(volume[1]) if len(volume) > 1 else 1.0
        if clipped < 0.01:
            print("    OK (input volume %d%%)" % round(level * 100))
            return
        new_level = round(level * 0.8, 2)
        if new_level < 0.2:
            break
        print("    recording is clipped (%d%%), input volume %d%% -> %d%%"
              % (clipped * 100, level * 100, new_level * 100))
        run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", str(new_level)])
    print("! The microphone is still clipped; lower the input "
          "volume in the sound settings.")


# -------------------------------------------------------------- uninstall

def uninstall():
    print("==> Removing dictation")
    subprocess.run(["systemctl", "--user", "disable", "--now",
                    "dictation.service"])
    for path in (SERVICE_FILE, LAUNCHER):
        if os.path.exists(path):
            os.remove(path)
    subprocess.run(["systemctl", "--user", "daemon-reload"])

    uninstall_hotkey_kde()

    shutil.rmtree(APP_DIR, ignore_errors=True)
    run(["sudo", "rm", "-f", UDEV_RULE_FILE, MODULES_FILE])
    print("\nRemoved. Kept: %s and %s." % (CONFIG_FILE, API_KEY_FILE))


def main():
    parser = argparse.ArgumentParser(
        description="Set up voice typing (Super+H) on Debian 13 + KDE "
                    "Plasma.")
    parser.add_argument("--backend", choices=["groq", "openai"],
                        help="speech-to-text service (default: groq)")
    parser.add_argument("--api-key", action="store_true",
                        help="ask for the API key even if one is saved")
    parser.add_argument("--language",
                        help="language you dictate in, e.g. en or hu "
                             "('' = detect it; default: the desktop's "
                             "language, %s)" % desktop_language())
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    if os.geteuid() == 0:
        sys.exit("Run as your normal user, without sudo "
                 "(the script asks for the password when needed).")
    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":"):
        sys.exit("This script is for a KDE Plasma desktop session.")

    if args.uninstall:
        uninstall()
        return

    install_system()
    install_app()
    install_config(args)
    install_service()
    install_hotkey_kde()
    check_microphone()
    print("""
==> Done. Press Super+H, speak, and pause: the text appears in the focused
window. Press Super+H again to stop (it also stops after a longer silence).

Settings: %s (after editing: systemctl --user restart dictation)
Log:      journalctl --user -u dictation -f
""" % CONFIG_FILE)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit("Command failed: %s" % exc)
