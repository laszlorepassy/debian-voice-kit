#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install-ebook-reader.py
=======================
Installs an e-book reader that reads aloud with a natural neural voice, on
Debian 13 + KDE Plasma, in English, Hungarian or any other language Piper
has a voice for:

  1. Removes Debian's calibre package (8.5 in trixie; it has no Piper, so
     it can only read aloud with the robotic espeak-ng voice) and installs
     the latest calibre from calibre-ebook.com into /opt/calibre, with the
     official installer (it checks the download's SHA-512 signature).
     calibre has the Piper neural text-to-speech engine built in. The
     Python/Qt packages the Debian calibre pulled in are removed too, so
     only one calibre stays on the machine.
  2. Downloads the Piper voices of your language (English: lessac, ryan,
     amy; Hungarian: anna, berta, imre; 60-120 MB each) into calibre's voice
     folder, ~/.cache/calibre/piper-voices, so reading aloud does not stop
     to download on first use. The language is the one of your desktop;
     --lang picks others.
  3. Sets up Read aloud in ~/.config/calibre/tts.json: Piper engine and the
     first voice of the first language (--voice picks another one).
  4. Makes the E-book viewer the default app for ePub, MOBI, AZW3 and FB2
     files (PDF, HTML and plain text are left alone).
  5. Installs epub-to-mp3.py (next to this script) as the epub-to-mp3
     command in ~/.local/bin, and a Dolphin right-click action for EPUB
     files, "Make MP3 audiobook", which runs it in a terminal window.

Run it as your normal user (NOT with sudo); it asks for the sudo password.
Safe to run again: an installed /opt/calibre is kept, --update installs the
latest version over it.

Read aloud in the E-book viewer: right click > Read aloud, or Ctrl+S.
The speed and the voice: in the Read aloud bar > Configure.

USAGE:
    python3 install-ebook-reader.py
    python3 install-ebook-reader.py --lang hu en     # voices of both
    python3 install-ebook-reader.py --voice imre     # Read aloud voice
    python3 install-ebook-reader.py --list-voices    # every Piper voice
    python3 install-ebook-reader.py --update         # latest calibre
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER_URL = "https://download.calibre-ebook.com/linux-installer.sh"
INSTALL_DIR = "/opt"
CALIBRE = os.path.join(INSTALL_DIR, "calibre")
VOICES_JSON = os.path.join(CALIBRE, "resources", "piper-voices.json")
VOICES_DIR = os.path.expanduser("~/.cache/calibre/piper-voices")
TTS_CONFIG = os.path.expanduser("~/.config/calibre/tts.json")
# The voices downloaded for a language; the first one is the default.
# Other languages get their first three voices from calibre's list.
LANGUAGE_VOICES = {
    "en_US": ["lessac", "ryan", "amy"],
    "en_GB": ["alba", "alan", "cori"],
    "hu_HU": ["anna", "berta", "imre"],
}
# Piper qualities, best first; calibre uses the best one of a voice.
QUALITIES = ["high", "medium", "low", "x_low"]

# Debian packages of the old calibre, removed before the official install.
OLD_PACKAGES = ["calibre", "calibre-bin"]
# What the official calibre needs on Debian (calibre-ebook.com/download_linux).
APT_PACKAGES = ["xdg-utils", "xz-utils", "libegl1", "libopengl0",
                "libxcb-cursor0"]
# ffmpeg encodes the MP3 files of epub-to-mp3.
APT_PACKAGES += ["ffmpeg"]

MP3_COMMAND = os.path.expanduser("~/.local/bin/epub-to-mp3")
SERVICE_MENU = os.path.expanduser(
    "~/.local/share/kio/servicemenus/epub-to-mp3.desktop")
SERVICE_MENU_ENTRY = """[Desktop Entry]
Type=Service
MimeType=application/epub+zip;
Actions=makeMp3;

[Desktop Action makeMp3]
Name=Make MP3 audiobook
Name[hu]=MP3 hangoskönyv készítése
Icon=audio-x-generic
Exec={terminal} {command} %F
"""
# Title of the terminal window epub-to-mp3 runs in, by desktop language.
TERMINAL_TITLES = {"en": "MP3 audiobook", "hu": "MP3 hangoskönyv"}

VIEWER_DESKTOP = "calibre-ebook-viewer.desktop"
EBOOK_TYPES = [
    "application/epub+zip",
    "application/x-mobipocket-ebook",
    "application/x-mobi8-ebook",
    "text/fb2+xml",
]


def run(cmd, **kwargs):
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kwargs)


def desktop_locale():
    """The desktop's language, e.g. 'hu_HU' or 'en_US' ('en' if unknown)."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").split(":")[0].split(".")[0]
        if value and value not in ("C", "POSIX"):
            return value
    return "en"


def installed_packages(names):
    out = subprocess.run(["dpkg-query", "-W", "-f=${Package} ${Status}\n"]
                         + names, capture_output=True, text=True).stdout
    return [line.split()[0] for line in out.splitlines()
            if line.endswith(" installed")]


def install_calibre(update):
    print("==> Installing the packages calibre needs")
    old = installed_packages(OLD_PACKAGES)
    if old:
        # --autoremove also removes the ~110 Python/Qt packages it pulled
        # in; the official calibre brings its own copies of them.
        print("    removing Debian's calibre: %s" % " ".join(old))
        run(["sudo", "apt-get", "remove", "--autoremove", "-y"] + old)
    run(["sudo", "apt-get", "update"])
    run(["sudo", "apt-get", "install", "-y"] + APT_PACKAGES)

    if os.path.exists(os.path.join(CALIBRE, "calibre")) and not update:
        print("==> calibre is already in %s (--update for the latest)"
              % CALIBRE)
        return
    print("==> Installing the latest calibre into %s" % CALIBRE)
    with tempfile.NamedTemporaryFile(suffix=".sh") as f:
        with urllib.request.urlopen(INSTALLER_URL) as answer:
            f.write(answer.read())
        f.flush()
        run(["sudo", "sh", f.name, "install_dir=%s" % INSTALL_DIR])


def load_voices():
    """calibre's Piper voice list: {'hu_HU': {'anna': {quality: urls}}}."""
    with open(VOICES_JSON, "r", encoding="utf-8") as f:
        return json.load(f)["lang_map"]


def resolve_language(code, lang_map):
    """'hu' -> 'hu_HU', 'en' -> 'en_US', 'en_GB' -> 'en_GB'; None if Piper
    has no voice for it."""
    if code in lang_map:
        return code
    prefix = code.split("_")[0].lower() + "_"
    for known in list(LANGUAGE_VOICES) + sorted(lang_map):
        if known.startswith(prefix) and known in lang_map:
            return known
    return None


def voices_for(language, lang_map):
    return LANGUAGE_VOICES.get(language) or sorted(lang_map[language])[:3]


def download_voices(languages, voice, lang_map):
    for language in languages:
        names = voices_for(language, lang_map)
        if voice in lang_map[language] and voice not in names:
            names = names + [voice]
        print("==> Downloading the %s voices (%s) into %s"
              % (language, ", ".join(names), VOICES_DIR))
        os.makedirs(VOICES_DIR, exist_ok=True)
        for name in names:
            qualities = lang_map[language][name]
            quality = min(qualities, key=QUALITIES.index)
            urls = qualities[quality]
            # The file names calibre looks for: hu_HU-anna-medium.onnx(.json)
            model = os.path.join(VOICES_DIR, "%s-%s-%s.onnx"
                                 % (language, name, quality))
            for url, path in ((urls["config"], model + ".json"),
                              (urls["model"], model)):
                if os.path.isfile(path):
                    continue
                print("    %s" % os.path.basename(path))
                with urllib.request.urlopen(url) as answer, \
                        open(path + ".part", "wb") as f:
                    while True:
                        chunk = answer.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                os.rename(path + ".part", path)


def configure_read_aloud(language, voice):
    print("==> Read aloud: Piper, voice %s (%s) in %s"
          % (voice, language, TTS_CONFIG))
    config = {}
    if os.path.isfile(TTS_CONFIG):
        with open(TTS_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)
    config["engine"] = "piper"
    piper = config.setdefault("engines", {}).setdefault("piper", {})
    piper["voice"] = "%s:%s" % (language, voice)
    os.makedirs(os.path.dirname(TTS_CONFIG), exist_ok=True)
    with open(TTS_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def install_mp3_tool():
    print("==> Installing epub-to-mp3 and its Dolphin action")
    os.makedirs(os.path.dirname(MP3_COMMAND), exist_ok=True)
    shutil.copy(os.path.join(HERE, "epub-to-mp3.py"), MP3_COMMAND)
    os.chmod(MP3_COMMAND, 0o755)
    # A terminal window shows the progress; it stays open at the end.
    title = TERMINAL_TITLES.get(desktop_locale().split("_")[0],
                                TERMINAL_TITLES["en"])
    if shutil.which("kitty"):
        terminal = "kitty --hold --title \"%s\"" % title
    else:
        terminal = "konsole --hold -e"
    os.makedirs(os.path.dirname(SERVICE_MENU), exist_ok=True)
    with open(SERVICE_MENU, "w", encoding="utf-8") as f:
        f.write(SERVICE_MENU_ENTRY.format(terminal=terminal,
                                          command=MP3_COMMAND))
    # Plasma 6 only runs local service menus that are executable.
    os.chmod(SERVICE_MENU, 0o755)
    print("    Dolphin: right-click an EPUB > Make MP3 audiobook")


def list_voices(lang_map):
    for language in sorted(lang_map):
        print("%-6s %s" % (language, ", ".join(sorted(lang_map[language]))))


def main():
    parser = argparse.ArgumentParser(
        description="Install the latest calibre with natural Piper voices "
                    "for Read aloud.")
    parser.add_argument("--lang", nargs="+", metavar="LANG",
                        help="languages to download voices for, e.g. en, "
                             "hu or en_GB; the first one is read aloud "
                             "(default: the desktop's language, %s)"
                             % desktop_locale())
    parser.add_argument("--voice",
                        help="Read aloud voice of the first language, e.g. "
                             "lessac, ryan, amy (en) or anna, berta, imre "
                             "(hu); default: the first of these")
    parser.add_argument("--list-voices", action="store_true",
                        help="list the Piper voices of every language "
                             "(needs calibre installed)")
    parser.add_argument("--update", action="store_true",
                        help="install the latest calibre even if one is "
                             "installed")
    args = parser.parse_args()

    if os.geteuid() == 0:
        sys.exit("Run as your normal user, without sudo "
                 "(the script asks for the password when needed).")
    if args.list_voices:
        if not os.path.isfile(VOICES_JSON):
            sys.exit("calibre is not installed yet; run this script "
                     "without --list-voices first.")
        list_voices(load_voices())
        return

    install_calibre(args.update)
    lang_map = load_voices()
    languages = []
    for code in args.lang or [desktop_locale()]:
        language = resolve_language(code, lang_map)
        if language is None:
            print("! Piper has no voice for '%s'; see --list-voices" % code)
        elif language not in languages:
            languages.append(language)
    if not languages:
        languages = ["en_US"]
    voice = args.voice or voices_for(languages[0], lang_map)[0]
    if voice not in lang_map[languages[0]]:
        sys.exit("No voice '%s' for %s; the voices: %s"
                 % (voice, languages[0],
                    ", ".join(sorted(lang_map[languages[0]]))))

    download_voices(languages, voice, lang_map)
    configure_read_aloud(languages[0], voice)
    install_mp3_tool()

    print("==> Opening e-books with the calibre E-book viewer")
    run(["xdg-mime", "default", VIEWER_DESKTOP] + EBOOK_TYPES)

    print("\nDone. Open an ePub file, or run: ebook-viewer book.epub")
    print("Read aloud: right click > Read aloud (Ctrl+S).")
    print("MP3 audiobook: right-click an EPUB in Dolphin > Make MP3 "
          "audiobook, or: epub-to-mp3 book.epub")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit("Command failed: %s" % " ".join(e.cmd))
