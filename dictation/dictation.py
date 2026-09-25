#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dictation.py
============
Voice typing for KDE Plasma (Wayland), similar to Win+H on
Windows. Speech is transcribed in the cloud by the Groq (Whisper large-v3)
or OpenAI API: fast and accurate, but the recorded speech is sent to that
service, and it needs an internet connection and an API key.

Pauses are detected locally with the Silero voice activity detector that
comes with the faster-whisper package (no Whisper model is downloaded).

Press the hotkey (Super+H) and speak. Whenever you pause, the sentence is
transcribed and pasted into the focused window. Press the hotkey again to
stop; it also stops by itself after a longer silence.

This file is both the background service and its command-line client:

    dictation.py daemon     run the service (started by systemd, see below)
    dictation.py toggle     start or stop dictation (bound to Super+H)
    dictation.py start | stop | status

It is installed by setup-dictation.py, which also creates the systemd user
service, the Super+H shortcut and the config file:

    ~/.config/dictation/config.json
    ~/.config/dictation/api-key        (cloud API key, readable only by you)

How text gets into the window: the text is put on the clipboard (and the
primary selection) with wl-copy, then Shift+Insert is sent through a
virtual keyboard (/dev/uinput). Shift+Insert pastes in GTK, Qt, browser,
LibreOffice and terminal windows alike. The previous
clipboard text is restored afterwards.
"""

import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

CONFIG_FILE = os.path.expanduser("~/.config/dictation/config.json")
API_KEY_FILE = os.path.expanduser("~/.config/dictation/api-key")
SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "dictation.sock")

CLOUD_BACKENDS = {
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions",
             "whisper-large-v3"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions",
               "gpt-4o-transcribe"),
}

DEFAULT_CONFIG = {
    # "groq" or "openai"; the key goes into API_KEY_FILE
    "backend": "groq",
    # Model name; empty = the default of the backend (see CLOUD_BACKENDS).
    "model": "",
    # Language code (en, hu, de, ...), or "" for automatic detection;
    # setup-dictation.py sets it to the desktop's language.
    "language": "",
    # A pause this long ends a sentence, which is then transcribed.
    "pause_seconds": 1.5,
    # Dictation stops by itself after this much silence.
    "stop_after_silence_seconds": 10,
    # A sentence is cut after this long even without a pause.
    "max_segment_seconds": 20,
}

SAMPLE_RATE = 16000

# Whisper sometimes "hears" these on noise; drop them when they are alone.
HALLUCINATIONS = {
    "köszönöm a figyelmet", "köszönöm, hogy megnézted",
    "köszönöm, hogy megnézted a videót", "feliratok", "felirat",
    "thank you", "thanks for watching", "thank you for watching",
    "you", "amara.org",
}


# Notification texts in the desktop's language (English if not listed).
MESSAGES = {
    "en": {
        "title": "Dictation",
        "listening": "Listening... (Super+H to stop)",
        "transcribing": "Transcribing...",
        "not_running": "Dictation service is not running. "
                       "Start it: systemctl --user start dictation",
        "no_key": "No API key: run setup-dictation.py --api-key",
        "bad_key": "Invalid API key: run setup-dictation.py --api-key",
        "limit": "API limit reached, try again later",
        "api_error": "API error %s",
        "offline": "No connection to the speech service",
    },
    "hu": {
        "title": "Diktálás",
        "listening": "Figyelek... (Super+H: leállítás)",
        "transcribing": "Leírom...",
        "not_running": "A diktálás szolgáltatás nem fut. "
                       "Indítás: systemctl --user start dictation",
        "no_key": "Nincs API-kulcs: futtasd: setup-dictation.py --api-key",
        "bad_key": "Érvénytelen API-kulcs: futtasd: "
                   "setup-dictation.py --api-key",
        "limit": "Elérted az API korlátját, próbáld újra később",
        "api_error": "API-hiba: %s",
        "offline": "Nem érem el a beszédfelismerő szolgáltatást",
    },
}


def desktop_language():
    """The desktop's language as a two-letter code, e.g. 'hu' or 'en'."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").split(":")[0].split(".")[0]
        if value and value not in ("C", "POSIX"):
            return value.split("_")[0].lower()
    return "en"


def tr(name):
    return MESSAGES.get(desktop_language(), MESSAGES["en"])[name]


def load_config():
    config = dict(DEFAULT_CONFIG)
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    return config


# ---------------------------------------------------------------- client

def send_command(command):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(SOCKET_PATH)
    except OSError:
        notify_once(tr("not_running"))
        sys.exit("Dictation service is not running "
                 "(systemctl --user status dictation).")
    s.sendall(command.encode() + b"\n")
    reply = s.makefile().readline().strip()
    s.close()
    return reply


def notify_once(body):
    subprocess.run(["notify-send", "-a", tr("title"), tr("title"), body],
                   stderr=subprocess.DEVNULL)


# ---------------------------------------------------------- notifications

class Notification:
    """One notification that is updated in place, then closed.

    A "sticky" notification uses critical urgency, which Plasma keeps
    on screen until it is closed, so it stays visible for the whole
    recording.
    """

    def __init__(self):
        self.id = 0
        self.lock = threading.Lock()

    def show(self, body, sticky=True):
        with self.lock:
            self._show(body, sticky)

    def _show(self, body, sticky):
        r = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.freedesktop.Notifications",
             "--object-path", "/org/freedesktop/Notifications",
             "--method", "org.freedesktop.Notifications.Notify",
             tr("title"), str(self.id), "audio-input-microphone",
             tr("title"), body, "[]",
             "{'transient': <true>, 'urgency': <byte %d>}"
             % (2 if sticky else 1), "0"],
            capture_output=True, text=True)
        # The reply looks like "(uint32 42,)".
        match = re.search(r"uint32 (\d+)", r.stdout)
        if r.returncode == 0 and match:
            self.id = int(match.group(1))

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.id:
            subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.freedesktop.Notifications",
                 "--object-path", "/org/freedesktop/Notifications",
                 "--method", "org.freedesktop.Notifications.CloseNotification",
                 str(self.id)], capture_output=True)
            self.id = 0


# ---------------------------------------------------------- virtual keyboard

class VirtualKeyboard:
    """Minimal /dev/uinput keyboard (no extra Python packages needed)."""

    UI_SET_EVBIT = 0x40045564
    UI_SET_KEYBIT = 0x40045565
    UI_DEV_CREATE = 0x5501
    EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
    KEY_LEFTSHIFT, KEY_INSERT = 42, 110

    def __init__(self):
        import fcntl
        import struct
        self.struct = struct
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, self.UI_SET_EVBIT, self.EV_KEY)
        for key in (self.KEY_LEFTSHIFT, self.KEY_INSERT):
            fcntl.ioctl(self.fd, self.UI_SET_KEYBIT, key)
        # struct uinput_user_dev: name, input_id, ff_effects_max, abs arrays
        dev = struct.pack("80sHHHHi", b"dictation-virtual-keyboard",
                          0x06, 0x1234, 0x5678, 1, 0) + bytes(64 * 4 * 4)
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, self.UI_DEV_CREATE)
        time.sleep(0.5)  # let the compositor pick up the new device

    def _event(self, etype, code, value):
        os.write(self.fd, self.struct.pack("llHHi", 0, 0, etype, code, value))

    def _key(self, code, value):
        self._event(self.EV_KEY, code, value)
        self._event(self.EV_SYN, self.SYN_REPORT, 0)
        time.sleep(0.02)

    def shift_insert(self):
        self._key(self.KEY_LEFTSHIFT, 1)
        self._key(self.KEY_INSERT, 1)
        self._key(self.KEY_INSERT, 0)
        self._key(self.KEY_LEFTSHIFT, 0)


# ---------------------------------------------------------------- clipboard

# KDE Plasma offers the Wayland data-control protocol, so wl-copy and
# wl-paste work without taking the keyboard focus.

WL_TEXT = "text/plain;charset=utf-8"


def clipboard_get():
    r = subprocess.run(["wl-paste", "--list-types"],
                       capture_output=True, text=True)
    if r.returncode != 0 or WL_TEXT not in r.stdout.split():
        return None
    r = subprocess.run(["wl-paste", "--no-newline", "--type", WL_TEXT],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def clipboard_set(data, selection="clipboard"):
    cmd = ["wl-copy", "--type", WL_TEXT]
    if selection == "primary":
        cmd.append("--primary")
    subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# ------------------------------------------------------------------ daemon

class Dictation:
    def __init__(self, config):
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        import numpy
        self.np = numpy
        self.get_speech_timestamps = get_speech_timestamps
        self.config = config
        self.vad_options = VadOptions(
            min_silence_duration_ms=int(config["pause_seconds"] * 1000),
            speech_pad_ms=200)
        if config["backend"] not in CLOUD_BACKENDS:
            sys.exit("Unknown backend in %s: %s"
                     % (CONFIG_FILE, config["backend"]))
        self.api_key = ""
        if os.path.isfile(API_KEY_FILE):
            with open(API_KEY_FILE, "r", encoding="utf-8") as f:
                self.api_key = f.read().strip()
        self.keyboard = VirtualKeyboard()
        self.notification = Notification()
        self.lock = threading.Lock()
        self.recording = False
        self.recorder = None
        self.session = 0
        self.jobs = []                 # pending audio segments
        self.jobs_ready = threading.Condition()
        self.saved_clipboard = None
        self.pasted_in_session = False
        threading.Thread(target=self.transcribe_worker, daemon=True).start()
        print("Ready.", flush=True)

    # --- recording --------------------------------------------------------

    def start(self):
        with self.lock:
            if self.recording:
                return
            self.recording = True
            self.session += 1
            self.pasted_in_session = False
            self.saved_clipboard = clipboard_get()
            self.recorder = subprocess.Popen(
                ["pw-record", "--raw", "--rate", str(SAMPLE_RATE),
                 "--channels", "1", "--format", "s16", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            threading.Thread(target=self.record_loop,
                             args=(self.recorder, self.session),
                             daemon=True).start()
        self.notification.show(tr("listening"))

    def stop(self):
        with self.lock:
            if not self.recording:
                return
            self.recording = False
            recorder = self.recorder
        # Shown before the recorder stops, so finish_session() (which runs
        # after the last sentence is pasted) always closes it.
        self.notification.show(tr("transcribing"))
        recorder.terminate()

    def record_loop(self, recorder, session):
        np = self.np
        chunks = []                    # float32 arrays since the last cut
        pending = 0                    # samples since the last cut
        silence_since = time.monotonic()
        last_check = 0
        pause = int(self.config["pause_seconds"] * SAMPLE_RATE)
        max_len = int(self.config["max_segment_seconds"] * SAMPLE_RATE)
        stop_after = float(self.config["stop_after_silence_seconds"])

        while True:
            data = recorder.stdout.read(SAMPLE_RATE // 10 * 2)  # 0.1 s
            if data:
                chunks.append(np.frombuffer(data, dtype="<i2")
                              .astype(np.float32) / 32768.0)
                pending += len(data) // 2
            finished = not data

            if not finished and pending - last_check < SAMPLE_RATE // 4:
                continue
            last_check = pending
            audio = np.concatenate(chunks) if chunks else \
                np.zeros(0, dtype=np.float32)
            speech = self.get_speech_timestamps(audio, self.vad_options) \
                if len(audio) else []

            cut = 0
            if speech:
                silence_since = time.monotonic() - \
                    (len(audio) - speech[-1]["end"]) / SAMPLE_RATE
                if finished or len(audio) - speech[-1]["end"] >= pause:
                    cut = len(audio)
                elif len(audio) >= max_len:
                    # Cut at the last pause inside, or everything if none.
                    cut = speech[-2]["end"] if len(speech) > 1 else len(audio)
            elif len(audio) >= max_len or finished:
                cut = len(audio)      # only silence: drop it

            if cut:
                segment, rest = audio[:cut], audio[cut:]
                if self.get_speech_timestamps(segment, self.vad_options):
                    self.queue(session, segment)
                chunks = [rest] if len(rest) else []
                pending = last_check = len(rest)

            if finished:
                break
            if time.monotonic() - silence_since >= stop_after:
                self.stop()
        recorder.wait()
        self.queue(session, None)      # end of session marker

    # --- transcription ----------------------------------------------------

    def transcribe_cloud(self, segment, prompt):
        url, default_model = CLOUD_BACKENDS[self.config["backend"]]
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((self.np.clip(segment, -1, 1) * 32767)
                          .astype("<i2").tobytes())
        fields = {"model": self.config["model"] or default_model,
                  "response_format": "json",
                  "temperature": "0"}
        if self.config["language"]:
            fields["language"] = self.config["language"]
        if prompt:
            fields["prompt"] = prompt
        boundary = uuid.uuid4().hex
        body = b""
        for name, value in fields.items():
            body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\""
                     "\r\n\r\n%s\r\n" % (boundary, name, value)).encode()
        body += ("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                 "filename=\"speech.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
                 % boundary).encode() + wav.getvalue() + \
            ("\r\n--%s--\r\n" % boundary).encode()
        request = urllib.request.Request(url, data=body, headers={
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": "dictation.py"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response).get("text", "").strip()

    def transcribe(self, segment, prompt):
        """Return the text, or None after showing the error."""
        if not self.api_key:
            Notification().show(tr("no_key"), sticky=False)
            return None
        try:
            return self.transcribe_cloud(segment, prompt)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            print("API error %s: %s" % (exc.code, detail), flush=True)
            if exc.code in (401, 403):
                message = tr("bad_key")
            elif exc.code == 429:
                message = tr("limit")
            else:
                message = tr("api_error") % exc.code
        except (urllib.error.URLError, OSError) as exc:
            print("API unreachable: %s" % exc, flush=True)
            message = tr("offline")
        # A separate, normal notification, so the status stays visible.
        Notification().show(message, sticky=False)
        return None

    def queue(self, session, segment):
        with self.jobs_ready:
            self.jobs.append((session, segment))
            self.jobs_ready.notify()

    def transcribe_worker(self):
        previous_text = ""
        while True:
            with self.jobs_ready:
                while not self.jobs:
                    self.jobs_ready.wait()
                session, segment = self.jobs.pop(0)

            if segment is None:
                if session == self.session and not self.recording:
                    self.finish_session()
                previous_text = ""
                continue

            try:
                text = self.transcribe(segment, previous_text[-200:])
            except Exception as exc:  # keep the service alive
                print("Transcription failed: %s" % exc, flush=True)
                continue

            if not text or text.lower().strip(" .!?") in HALLUCINATIONS:
                continue
            print("> %s" % text, flush=True)
            previous_text = text
            self.paste(session, text)

    def paste(self, session, text):
        if self.pasted_in_session:
            text = " " + text
        data = text.encode("utf-8")
        clipboard_set(data)
        clipboard_set(data, "primary")
        time.sleep(0.15)
        self.keyboard.shift_insert()
        self.pasted_in_session = True

    def finish_session(self):
        self.notification.close()
        if self.pasted_in_session and self.saved_clipboard is not None:
            time.sleep(0.5)
            clipboard_set(self.saved_clipboard)
        self.saved_clipboard = None

    # --- control socket ---------------------------------------------------

    def serve(self):
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        server.listen(4)
        while True:
            conn, _ = server.accept()
            with conn:
                command = conn.makefile().readline().strip()
                if command == "toggle":
                    self.stop() if self.recording else self.start()
                elif command == "start":
                    self.start()
                elif command == "stop":
                    self.stop()
                reply = "recording" if self.recording else "idle"
                conn.sendall(reply.encode() + b"\n")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "daemon":
        Dictation(load_config()).serve()
    elif command in ("toggle", "start", "stop", "status"):
        print(send_command(command))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
